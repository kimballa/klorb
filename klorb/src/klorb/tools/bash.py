# © Copyright 2026 Aaron Kimball
"""A Tool that runs a shell command requested by the model, gated by two layers of defense: a
`CommandRules`/`readDirs`/`writeDirs`-driven allow/ask/deny classification of the parsed command
(never a regexp/lexical one — see `klorb.permissions.shell_parse`), and (once
docs/plans/ready/004-bash-permissions-and-bash-tool.md's bubblewrap work lands) an OS-level
sandbox boundary. See that plan doc for the full design; this module implements every part of it
that doesn't require building the actual `bwrap` argv (see `klorb.sandbox`).
"""

import atexit
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from klorb.permissions.command_access import CommandPermissionsTable
from klorb.permissions.directory_access import DirRules
from klorb.permissions.shell_parse import BashCommandAnalysis, RedirectTarget, parse_command
from klorb.permissions.table import Verdict, raise_if_not_allowed, stricter_verdict
from klorb.permissions.workspace import evaluate_write, resolve_and_evaluate_read, resolve_within_workspace
from klorb.sandbox import bwrap_available, detect_bwrap_unavailable_reason
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, truncate_lines

logger = logging.getLogger(__name__)

_TMP_DIR_PREFIX = "klorb-bash-"

ConsiderVerdict = Callable[[Verdict, "Path | None", bool], None]
"""Callback shape `BashTool._evaluate`/`_evaluate_redirect` use to fold one verdict/path/
is-write contribution into the running overall verdict — see `BashTool._evaluate`."""

_warned_sessions: set[int] = set()
"""`id(session_config)` for every session `BashTool` has already emitted its one-time
sandbox-fallback notice for (see `_sandbox_notice`) — a session gets a fresh `SessionConfig`
instance (`ProcessConfig.session.model_copy()`) at startup and on every `/clear`, so keying on
object identity naturally resets this exactly at session boundaries with no new plumbing. Not
meant to survive process restarts or to be a security control — purely to avoid repeating an
informational notice on every call within the same session."""


def build_bash_env(session_config: SessionConfig) -> dict[str, str]:
    """Build the environment `BashTool` runs a command with: `HOME`/`USER` are always shared
    from the klorb process's own environment (per the plan's "pass-thru" section), followed by
    every `session_config.share_env` name that's actually set in the klorb process's
    environment, followed by `session_config.set_env`'s overrides (applied last, so they shadow
    a shared value for the same name). Everything else in the klorb process's own environment is
    left out, mirroring `bwrap --clearenv`'s intent even though no `bwrap` boundary actually
    enforces it yet (see `klorb.sandbox`) — `subprocess.Popen(..., env=...)` receives exactly
    this dict, not `None` (which would inherit the entire parent environment), so the
    least-privilege intent doesn't silently evaporate while sandboxing is unimplemented.
    """
    env: dict[str, str] = {}
    for name in ("HOME", "USER"):
        if name in os.environ:
            env[name] = os.environ[name]
    for name in session_config.share_env:
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(session_config.set_env)
    return env


def _sandbox_notice() -> str:
    """One-time, human-readable explanation of why this command ran without an OS-level sandbox
    boundary — tailored by `klorb.sandbox.detect_bwrap_unavailable_reason()` when `bwrap` itself
    is the blocker, or noting plainly that klorb's sandboxed execution isn't built yet when
    `bwrap` would otherwise work. See the plan's "One-time warning" section."""
    if not bwrap_available():
        reason = detect_bwrap_unavailable_reason()
        if reason == "missing_binary":
            detail = (
                "Sandbox layer unavailable; cannot launch a sandboxed shell. "
                "Run `sudo apt-get install bubblewrap`.")
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
    dev/cloud environment (the plan's "process outcome" section anticipated only the first):

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
`_strip_bash_shell_noise`) — this is filtering known harness-induced noise, not classifying a
command's safety, so it doesn't conflict with this plan's "no regexp-based classification" rule
elsewhere (see the plan doc's "env vars" section)."""


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
            "stderr_file if the output is large), and runtime in seconds."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        command = args["command"]
        if not command.strip():
            raise ValueError("command must not be empty")
        logger.debug("Bash %r", command)

        analysis = parse_command(command, self._shfmt_command)
        overall_verdict, overall_path, overall_is_write = self._evaluate(analysis)
        raise_if_not_allowed(
            overall_verdict, resource_description=f"run shell command: {command}",
            path=overall_path, is_write=overall_is_write)

        return self._execute(command)

    def _evaluate(self, analysis: BashCommandAnalysis) -> tuple[Verdict, Path | None, bool]:
        """Combine every simple command's `CommandPermissionsTable` verdict, every redirection
        target's directory-access verdict, and every reason the walker itself forced an "ask"
        into one overall `(verdict, path, is_write)` via `stricter_verdict` — never more
        permissive than any single contributor. `path`/`is_write` carry the specific resource
        associated with the strictest contributor found, if any (a redirection target), so a
        real directory-access "ask" still threads through `Session`'s existing interactive-grant
        flow exactly like `EditFile`'s does; a bare command-pattern "ask" (no filesystem
        resource involved) carries no `path`, which fails closed today per `Session._run_tool_calls`
        (an ask with `path=None` always denies, regardless of `permission_framework`) — command-
        rule-specific interactive grants are future work, not built here.
        """
        command_table = CommandPermissionsTable(self.context.session_config.command_rules)

        overall: Verdict = "allow"
        overall_path: Path | None = None
        overall_is_write = True

        def consider(verdict: Verdict, path: Path | None, is_write: bool) -> None:
            nonlocal overall, overall_path, overall_is_write
            stricter = stricter_verdict(verdict, overall)
            if stricter != overall:
                overall, overall_path, overall_is_write = stricter, path, is_write
            elif stricter == verdict and overall_path is None and path is not None:
                overall_path, overall_is_write = path, is_write

        for argv in analysis.simple_commands:
            verdict = command_table.evaluate(argv)
            consider(verdict if verdict is not None else "ask", None, True)

        for _reason in analysis.forced_ask_reasons:
            consider("ask", None, True)

        for redirect in analysis.redirects:
            self._evaluate_redirect(redirect, consider)

        if analysis.forced_ask_reasons:
            logger.info("Bash command forced to 'ask': %s", analysis.forced_ask_reasons)

        return overall, overall_path, overall_is_write

    def _evaluate_redirect(self, redirect: RedirectTarget, consider: ConsiderVerdict) -> None:
        if redirect.direction == "write":
            path = resolve_within_workspace(self.context, redirect.target)
            consider(evaluate_write(self.context, path), path, True)
        else:
            path, verdict = resolve_and_evaluate_read(self.context, redirect.target)
            consider(verdict, path, False)

    def _execute(self, command: str) -> dict[str, Any]:
        workspace_root = self.context.session_config.workspace.path.resolve()
        env = build_bash_env(self.context.session_config)
        home = env.get("HOME", str(Path.home()))
        rcfile = str(Path(home) / ".bashrc")
        full_command = f"unset PS1; unset PS2; {command}"
        argv = [self._bash_command, "--rcfile", rcfile, "-i", "-c", full_command]

        tmp_dir = Path(tempfile.mkdtemp(prefix=_TMP_DIR_PREFIX))
        atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
        stdout_path = tmp_dir / "stdout"
        stderr_path = tmp_dir / "stderr"
        for path in (stdout_path, stderr_path):
            path.touch()
            os.chmod(path, 0o600)

        session_id = id(self.context.session_config)
        sandbox_notice = None
        if session_id not in _warned_sessions:
            _warned_sessions.add(session_id)
            sandbox_notice = _sandbox_notice()

        start = time.monotonic()
        with open(stdout_path, "wb") as stdout_f, open(stderr_path, "wb") as stderr_f:
            try:
                process = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL, stdout=stdout_f, stderr=stderr_f,
                    env=env, cwd=str(workspace_root))
            except FileNotFoundError as exc:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise ValueError(f"{self._bash_command!r} not found; cannot run command") from exc

            timed_out = False
            try:
                process.wait(timeout=self._timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
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
