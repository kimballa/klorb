# © Copyright 2026 Aaron Kimball
"""A Tool that runs a shell command requested by the model, gated by two layers of defense: a
`CommandRules`/`readDirs`/`writeDirs`-driven allow/ask/deny classification of the parsed command
(never a regexp/lexical one — see `klorb.permissions.shell_parse`), and (once its argv
construction lands — see `klorb.sandbox`) an OS-level `bwrap` sandbox boundary. See
docs/specs/bash-tool-and-command-permissions.md for the full design.
"""

import atexit
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from klorb.permissions.command_access import CommandPermissionsTable
from klorb.permissions.directory_access import DirRules
from klorb.permissions.shell_parse import BashCommandAnalysis, RedirectTarget, parse_command
from klorb.permissions.table import MultiPermissionAskRequired, PermissionAskItem, Verdict
from klorb.permissions.workspace import evaluate_write, resolve_and_evaluate_read, resolve_within_workspace
from klorb.sandbox import bwrap_available, detect_bwrap_unavailable_reason
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, truncate_lines

logger = logging.getLogger(__name__)

_TMP_DIR_PREFIX = "klorb-bash-"

_TOOL_STATE_KEY = "Bash"
_SANDBOX_WARNED_KEY = "sandbox_warned"
"""`session.tool_state["Bash"]["sandbox_warned"]`: whether the one-time sandbox-fallback notice
(see `_sandbox_notice`) has already been emitted for this session — `session.tool_state` starts
empty for every new `Session`, so this naturally resets at session boundaries (startup, `/clear`)
with no extra plumbing. Not meant to survive process restarts or to be a security control —
purely to avoid repeating an informational notice on every call within the same session."""


def build_bash_env(session_config: SessionConfig, bash_command: str) -> dict[str, str]:
    """Build the environment `BashTool` runs a command with: `HOME`/`USER` are always shared
    from the klorb process's own environment, `WORKSPACE_ROOT` is always set to the resolved
    workspace root and `SHELL`/`BASH` are always set to `bash_command` (unconditionally — not
    gated on any config, and not the klorb process's own `$SHELL`/`$BASH` if any — this is the
    actual bash binary the command runs under, per `ProcessConfig.bash_command`), followed by
    every `session_config.share_env` name that's actually set in the klorb process's environment,
    followed by `session_config.set_env`'s overrides (applied last, so they shadow a shared or
    always-set value for the same name). Everything else in the klorb process's own environment
    is left out, mirroring `bwrap --clearenv`'s intent even though no `bwrap` boundary actually
    enforces it yet (see `klorb.sandbox`) — `subprocess.Popen(..., env=...)` receives exactly
    this dict, not `None` (which would inherit the entire parent environment), so the
    least-privilege intent doesn't silently evaporate while sandboxing is unimplemented.
    """
    env: dict[str, str] = {}
    for name in ("HOME", "USER"):
        if name in os.environ:
            env[name] = os.environ[name]
    env["WORKSPACE_ROOT"] = str(session_config.workspace.path.resolve())
    env["SHELL"] = bash_command
    env["BASH"] = bash_command
    for name in session_config.share_env:
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(session_config.set_env)
    return env


def _sandbox_notice() -> str:
    """One-time, human-readable explanation of why this command ran without an OS-level sandbox
    boundary — tailored by `klorb.sandbox.detect_bwrap_unavailable_reason()` when `bwrap` itself
    is the blocker, or noting plainly that klorb's sandboxed execution isn't built yet when
    `bwrap` would otherwise work."""
    if not bwrap_available():
        reason = detect_bwrap_unavailable_reason()
        if reason == "missing_binary":
            detail = (
                "Sandbox layer unavailable; cannot launch a sandboxed shell. "
                "Run `sudo apt-get install bubblewrap`, then restart klorb.")
        else:
            detail = (
                "Sandbox layer unavailable: this environment does not permit unprivileged "
                "sandboxing (nested container or restrictive kernel policy).")
    else:
        detail = (
            "bubblewrap is available on this host, but klorb's sandboxed BashTool execution "
            "isn't implemented yet.")
    return (
        f"{detail} Running BashTool without an OS-level sandbox boundary; command and redirect "
        "permission checks (commandRules/readDirs/writeDirs) are still fully enforced.")


def _decode_exit(returncode: int) -> tuple[bool, str | None]:
    """Return `(success, failure_reason)` for a normal (non-timeout) process exit.

    A signal death shows up one of two ways here, verified directly against this project's own
    dev/cloud environment:

    * Positive, `128 + signum` — the outer `bash` we launch forked a real child for the target
      command (e.g. it's not the last thing in the script, or it's part of a pipeline/compound
      statement) and observed *that* child die by signal; bash itself then exits normally with
      the conventional `128 + signum` code, which is what `Popen.returncode` reports.
    * Negative, `-signum` — Python's ordinary direct-child-signaled convention. This happens
      when bash's own tail-call optimization `exec()`s directly into a simple trailing external
      command (no pipe/compound wrapping) instead of forking — confirmed empirically: `bash -c
      "unset PS1; unset PS2; python3 -c '...os.kill(os.getpid(), signal.SIGSEGV)'"` reports a
      *negative* `Popen.returncode`, not `139`, because bash's own process image was replaced by
      the target's — there is no separate bash process left to translate the signal into a
      normal exit. Both shapes are decoded the same way here so neither is mistaken for "exited
      normally with a large status code".
    """
    if returncode == 0:
        return True, None
    if returncode < 0:
        signum = -returncode
        try:
            return False, f"Process terminated by signal {signal.Signals(signum).name} ({signum})"
        except ValueError:
            return False, f"Process terminated by signal {signum}"
    if returncode > 128:
        try:
            signame = signal.Signals(returncode - 128).name
            return False, f"Process terminated by signal {signame} ({returncode - 128})"
        except ValueError:
            pass
    return False, "Process completed normally with non-zero status"


_BASH_NOISE_NO_JOB_CONTROL = "bash: no job control in this shell"
_BASH_NOISE_PROC_GROUP_PREFIX = "bash: cannot set terminal process group ("
_BASH_NOISE_PROC_GROUP_SUFFIX = "): Inappropriate ioctl for device"
"""The two fixed, content-independent lines `bash -i` always writes to stderr when launched
with no controlling tty (stdin from `/dev/null`, per this tool's design) — confirmed empirically
against this project's own dev/cloud environment. The process-group line's parenthesized number
varies by platform/session (observed as `-1` in some environments, an actual pid in others), so
it's matched by prefix/suffix rather than a single exact string; `no job control in this shell`
is otherwise always identical. Stripped from the *front* of captured stderr only (see
`_strip_bash_shell_noise`)."""


def _strip_bash_shell_noise(text: str) -> str:
    """Remove `bash -i`'s fixed startup noise lines from the front of `text`, stopping at the
    first line that doesn't match either known noise pattern — so real command output starting
    on the very next line is left untouched."""
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        stripped = lines[index].rstrip("\n")
        if stripped == _BASH_NOISE_NO_JOB_CONTROL:
            index += 1
            continue
        if stripped.startswith(_BASH_NOISE_PROC_GROUP_PREFIX) and stripped.endswith(
            _BASH_NOISE_PROC_GROUP_SUFFIX,
        ):
            index += 1
            continue
        break
    return "".join(lines[index:])


def _spill(path: Path, spill_bytes: int) -> tuple[str | None, str | None]:
    """Return `(inline_text, file_path)` for one captured stream: the decoded text if `path` is
    at most `spill_bytes` long, else `None` alongside `str(path)` so the model can `ReadFile`/
    `Grep` it instead of overrunning its context. Exactly one of the pair is non-`None`."""
    size = path.stat().st_size
    if size <= spill_bytes:
        return path.read_text(encoding="utf-8", errors="replace"), None
    return None, str(path)


class BashTool(Tool):
    """Runs a single shell command string on the model's behalf.

    The command is parsed via `shfmt --to-json` (`klorb.permissions.shell_parse.parse_command`),
    never a regexp/lexical classifier. Every simple command extracted from the parse (across
    pipelines, `&&`/`||`/`;` lists, subshells, command substitutions, control-flow bodies, etc.)
    is evaluated against `context.session_config.command_rules`
    (`klorb.permissions.command_access.CommandPermissionsTable`); every redirection's file target
    is evaluated against `readDirs`/`writeDirs` via the same `evaluate_write`/
    `resolve_and_evaluate_read` the file tools use. The overall verdict is the strictest of all
    of these (`klorb.permissions.table.stricter_verdict`) — never more permissive than any single
    contributor — enforced through the same `raise_if_not_allowed()` seam every other tool uses.

    Once permitted, the command runs via `bash --rcfile ${HOME}/.bashrc -i -c "unset PS1; unset
    PS2; <command>"`. Sandboxing this execution with `bwrap` is designed but not yet built (see
    `klorb.sandbox`); every call today runs unsandboxed, with a one-time notice the first time
    that happens in a given session (see `_sandbox_notice`) — the permission checks above are
    unaffected either way.

    Each call spawns its own fresh, non-persistent shell that exits when the command finishes —
    `apply()` requires the model to pass `shell_lifetime="command"` explicitly, the only
    currently-valid value, so a future persistent-shell mode (one shell surviving across several
    calls, keeping `cd`/exported-variable state) is an additive, opt-in change to this schema
    rather than a silent behavior change for existing callers.

    TODO(aaron): implement a `shell_lifetime="session"` mode that keeps one shell process alive
    across multiple `Bash` calls (persisting `cd`, exported variables, and background jobs
    between them), instead of always starting fresh.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._bash_command = context.process_config.bash_command
        self._timeout_seconds = context.process_config.bash_timeout_seconds
        self._spill_bytes = context.process_config.bash_spill_bytes
        self._shfmt_command = context.process_config.shfmt_command

    def name(self) -> str:
        return "Bash"

    def description(self) -> str:
        return (
            "Runs a shell command via bash. The command is parsed and every simple command/"
            "redirection it contains is checked against configured command and directory "
            "permission rules before anything runs; a command that isn't confidently "
            "classified (variable/command expansion, an unrecognized construct, backgrounding "
            "with '&', a heredoc/pipe feeding an interpreter other than cat/less/git) is denied "
            "or requires confirmation rather than silently allowed. Returns exit_status, "
            "success, failure_reason (null on success), stdout/stderr (or stdout_file/"
            "stderr_file if the output is large), and runtime in seconds. shell_lifetime must "
            "be \"command\": each call starts a fresh, non-persistent shell that exits when the "
            "command finishes; no state (cwd, env vars set by the command, background jobs) "
            "carries over to the next call."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                },
                "shell_lifetime": {
                    "type": "string",
                    "enum": ["command"],
                    "description": (
                        "Must be \"command\": this shell is not persistent across calls."
                    ),
                },
            },
            "required": ["command", "shell_lifetime"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        command = args["command"]
        shell_lifetime = args["shell_lifetime"]
        if shell_lifetime != "command":
            raise ValueError(f"shell_lifetime must be 'command', got {shell_lifetime!r}")
        if not command.strip():
            raise ValueError("command must not be empty")
        logger.debug("Bash %r", command)

        analysis = parse_command(command, self._shfmt_command)
        verdict, ask_items = self._classify(analysis)
        if verdict == "deny":
            raise PermissionError(f"Permission denied: run shell command: {command}")
        if verdict == "ask":
            raise MultiPermissionAskRequired(
                f"Permission requires confirmation: run shell command: {command}", items=ask_items)

        return self._execute(command)

    def _classify(self, analysis: BashCommandAnalysis) -> tuple[Verdict, list[PermissionAskItem]]:
        """Evaluate every simple command's `CommandPermissionsTable` verdict, every redirection
        target's directory-access verdict, and every reason the walker itself forced an "ask".

        A single `"deny"` anywhere short-circuits: the whole command is denied outright, with no
        `PermissionAskItem`s collected at all (nothing else needs asking about once the call is
        already going to be refused). Otherwise, every individual `"ask"` contributor becomes its
        own `PermissionAskItem` — not just the strictest one — so `Session` can ask about each
        in series (see `MultiPermissionAskRequired`) rather than collapsing a compound command
        with several independent concerns into one opaque prompt. `"allow"` (no deny, no ask
        items at all) means the whole command is permitted to run as-is.

        `context.permission_override`, when set, lets a previously-approved-once resource skip
        straight past its check on this one retried call (see `PermissionOverride`): a simple
        command whose exact argv is in `override.commands` never reaches `command_table`, and a
        forced-ask reason in `override.reasons` is dropped rather than turned into another
        `PermissionAskItem`. Redirection targets don't need an analogous check here — they're
        `Path`s, already covered by `override.paths` inside `evaluate_write`/
        `resolve_and_evaluate_read` themselves (see `_evaluate_redirect`).
        """
        command_table = CommandPermissionsTable(self.context.session_config.command_rules)
        override = self.context.permission_override
        ask_items: list[PermissionAskItem] = []
        denied = False

        for argv in analysis.simple_commands:
            if override is not None and tuple(argv) in override.commands:
                continue
            verdict = command_table.evaluate(argv)
            if verdict == "deny":
                denied = True
            elif verdict is None or verdict == "ask":
                ask_items.append(PermissionAskItem(f"run command: {' '.join(argv)}", command=argv))

        for reason in analysis.forced_ask_reasons:
            if override is not None and reason in override.reasons:
                continue
            ask_items.append(PermissionAskItem(reason))

        for redirect in analysis.redirects:
            redirect_verdict, path, is_write = self._evaluate_redirect(redirect)
            if redirect_verdict == "deny":
                denied = True
            elif redirect_verdict == "ask":
                action = "write to" if is_write else "read"
                ask_items.append(PermissionAskItem(f"{action} {path}", path=path, is_write=is_write))

        if denied:
            logger.info("Bash command denied outright: %s", analysis)
            return "deny", []
        if ask_items:
            logger.info("Bash command has %d item(s) needing confirmation", len(ask_items))
            return "ask", ask_items
        return "allow", []

    def _evaluate_redirect(self, redirect: RedirectTarget) -> tuple[Verdict, Path, bool]:
        if redirect.direction == "write":
            path = resolve_within_workspace(self.context, redirect.target)
            return evaluate_write(self.context, path), path, True
        path, verdict = resolve_and_evaluate_read(self.context, redirect.target)
        return verdict, path, False

    def _execute(self, command: str) -> dict[str, Any]:
        workspace_root = self.context.session_config.workspace.path.resolve()
        env = build_bash_env(self.context.session_config, self._bash_command)
        home = env.get("HOME", str(Path.home()))
        rcfile = str(Path(home) / ".bashrc")
        full_command = f"unset PS1; unset PS2; {command}"
        argv = [self._bash_command, "--rcfile", rcfile, "-i", "-c", full_command]

        tmp_dir = Path(tempfile.mkdtemp(prefix=_TMP_DIR_PREFIX))
        # Registered immediately after creation, before anything else can raise, so this
        # directory is always swept on process exit even if klorb dies mid-command.
        atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
        stdout_path = tmp_dir / "stdout"
        stderr_path = tmp_dir / "stderr"
        for path in (stdout_path, stderr_path):
            path.touch()
            os.chmod(path, 0o600)

        sandbox_notice = None
        if self.context.session is None:
            # No Session to dedupe against (e.g. a caller that built a ToolSetupContext
            # directly) -- show it every call rather than silently dropping it.
            sandbox_notice = _sandbox_notice()
        else:
            bash_state = self.context.session.tool_state.setdefault(_TOOL_STATE_KEY, {})
            if not bash_state.get(_SANDBOX_WARNED_KEY, False):
                bash_state[_SANDBOX_WARNED_KEY] = True
                sandbox_notice = _sandbox_notice()

        # stdout_path/stderr_path are only read back (noise-stripping, spill) after this `with`
        # block exits below -- by then process.wait()/kill()+wait() has already returned, so the
        # child has finished writing before our own file handles close and we reopen for reading.
        start = time.monotonic()
        with open(stdout_path, "wb") as stdout_f, open(stderr_path, "wb") as stderr_f:
            try:
                # start_new_session=True (setsid() in the child before exec) detaches the child
                # into a brand-new session with no controlling terminal at all -- not just no
                # controlling terminal *as far as its own stdin/stdout/stderr fds reveal* (those
                # are already redirected away from any tty above), but genuinely none, even if
                # this klorb process itself is running attached to a real terminal. Without this,
                # anything the sourced ~/.bashrc launches (a background ssh-agent/keychain job, a
                # prompt framework's setup, anything that opens /dev/tty by name) still shares
                # this process's session and can contend with it for the terminal's foreground
                # process group -- which can suspend the *klorb* process itself, not just the
                # child, when klorb is run interactively. setsid() also makes the child its own
                # process group leader (pgid == pid), which the timeout-kill path below relies on.
                process = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL, stdout=stdout_f, stderr=stderr_f,
                    env=env, cwd=str(workspace_root), start_new_session=True)
            except FileNotFoundError as exc:
                # stdout_f/stderr_f are still open here (this `with` block's __exit__ hasn't run
                # yet). Safe on POSIX regardless: unlink() only removes a directory entry, not the
                # underlying inode, so rmtree can remove both files and the now-empty directory
                # while fds to them are still live; the fds themselves stay valid until __exit__
                # closes them moments later, as this exception propagates out of the `with`.
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise ValueError(f"{self._bash_command!r} not found; cannot run command") from exc

            timed_out = False
            try:
                process.wait(timeout=self._timeout_seconds)
            except subprocess.TimeoutExpired:
                # Kill the whole process group, not just `process`'s own pid: a background job
                # the command (or its sourced ~/.bashrc) started -- and that survived bash's own
                # tail-call-exec optimization, so it's a distinct process from `process.pid` --
                # would otherwise outlive the timeout as an orphan. Safe because
                # start_new_session=True above made this child its own process group leader.
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                timed_out = True
        runtime = time.monotonic() - start

        if timed_out:
            success = False
            failure_reason: str | None = f"Command timed out at {self._timeout_seconds} seconds"
            exit_status = process.returncode if process.returncode is not None else -1
        else:
            exit_status = process.returncode
            success, failure_reason = _decode_exit(exit_status)

        stderr_path.write_text(
            _strip_bash_shell_noise(stderr_path.read_text(encoding="utf-8", errors="replace")),
            encoding="utf-8")

        stdout_text, stdout_file = _spill(stdout_path, self._spill_bytes)
        stderr_text, stderr_file = _spill(stderr_path, self._spill_bytes)

        if stdout_file is None and stderr_file is None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        else:
            self._grant_tmp_dir_read_access(tmp_dir)

        logger.info(
            "Bash command finished: exit_status=%s success=%s runtime=%.2fs",
            exit_status, success, runtime)

        result = {
            "command": command,
            "exit_status": exit_status,
            "success": success,
            "failure_reason": failure_reason,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "stdout_file": stdout_file,
            "stderr_file": stderr_file,
            "runtime": runtime,
        }
        if sandbox_notice is not None:
            result["sandbox_notice"] = sandbox_notice
        return result

    def _grant_tmp_dir_read_access(self, tmp_dir: Path) -> None:
        """Auto-grant read access to `tmp_dir` (this one invocation's own stdout/stderr capture
        directory, and nothing else — a fresh `mkdtemp()` per call) so a follow-up `ReadFile`/
        `Grep` call against a spilled `stdout_file`/`stderr_file` doesn't itself hit an "ask".
        Appends to the live `session_config.read_dirs.allow` (reassigning the whole `DirRules`,
        never mutating its list in place, per `DirRules`'s documented contract) — this is
        automatic harness bookkeeping for the harness's own scratch output, not a
        user-confirmed grant, so it bypasses `klorb.permissions.grant`'s interactive-grant flow
        entirely (there is no ask being answered here).
        """
        read_dirs = self.context.session_config.read_dirs
        self.context.session_config.read_dirs = DirRules(
            deny=list(read_dirs.deny), ask=list(read_dirs.ask), allow=[*read_dirs.allow, tmp_dir])

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        command = args.get("command", "?")
        if error is not None:
            return f"Bash: {command} failed: {error}"
        if not isinstance(result, dict):
            return f"Bash: {command}"
        status = "ok" if result.get("success") else f"exit {result.get('exit_status')}"
        return f"Bash: {command} ({status}, {result.get('runtime', 0):.2f}s)"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with inline `stdout`/`stderr` capped
        to 20 lines each — a chatty command's output could otherwise dump hundreds of lines
        here even when it stayed under `tools.bash.spillBytes`."""
        if error is not None or not isinstance(result, dict):
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        for key in ("stdout", "stderr"):
            value = capped_result.get(key)
            if isinstance(value, str):
                capped_result[key] = truncate_lines(value, 20)
        return super().detail_view(args, capped_result, error)
