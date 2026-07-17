# © Copyright 2026 Aaron Kimball
"""A Tool that runs a shell command requested by the model, gated by two layers of defense: a
`CommandRules`/`readDirs`/`writeDirs`-driven allow/ask/deny classification of the parsed command
(never a regexp/lexical one — see `klorb.permissions.shell_parse`), and an OS-level `bwrap`
sandbox boundary (`klorb.sandbox.build_bwrap_argv`) around the actual execution, with an
unsandboxed fallback where `bwrap` can't create a sandbox. See
docs/specs/bash-tool-and-command-permissions.md for the full design.
"""

import atexit
import logging
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable, Literal

from klorb.permissions.command_access import CommandPermissionsTable
from klorb.permissions.directory_access import DirRules
from klorb.permissions.shell_parse import BashCommandAnalysis, RedirectTarget, parse_command
from klorb.permissions.table import MultiPermissionAskRequired, PermissionAskItem, Verdict
from klorb.permissions.workspace import resolve_and_evaluate_read, resolve_and_evaluate_write
from klorb.sandbox import (
    SandboxDirs,
    allowed_dir_snapshot,
    build_bwrap_argv,
    bwrap_available,
    compute_sandbox_dirs,
    detect_bwrap_unavailable_reason,
    path_dirs_from_env,
)
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

_PERSISTENT_SHELL_KEY = "persistent_shell"
"""`session.tool_state["Bash"]["persistent_shell"]`: the single live `PersistentShell` (or
`None`) `shell_lifetime="session"`/`"new"` calls reuse — see `PersistentShell` and
`BashTool._execute_persistent`. At most one persistent shell exists per `Session` at a time."""

_STANDING_INTERJECTION_SUBJECT = "SessionTerminal"
"""`Session.register_standing_interjection()` subject key `BashTool` registers under whenever it
creates or reuses a persistent shell — see `_standing_interjection_provider` and
docs/adrs/standing-interjections-complement-one-shot-for-level-triggered-state.md."""

_TEARDOWN_SUBJECT = "Bash"
"""`Session.register_teardown()` subject key `BashTool` registers under for killing a live
persistent shell when the owning `Session` is closed — see `Session.close()`."""

_TIMEOUT_GRACE_SECONDS = 3.0
"""How long a persistent-shell command is given to finish after `SIGINT` before being escalated
to `SIGKILL` on timeout or cancellation — see `PersistentShell._run_raw`."""

_CANCEL_POLL_SECONDS = 0.1
"""How often the one-shot (`BashTool._execute`) and persistent-shell (`PersistentShell._run_raw`)
wait loops wake up to check `Session.active_cancel_event` while a command runs, so a Ctrl+C/
Escape interrupt is noticed promptly rather than only once the command's own output arrives or
its full `tools.bash.timeout` elapses. Mirrors `klorb.tui.shell`'s identical
`_POLL_INTERVAL_SECONDS` for the `!`-prefixed direct-shell feature."""

_CANCEL_FAILURE_REASON = (
    "Command canceled: the user pressed Ctrl+C/Escape to interrupt this tool call.")
"""`failure_reason` reported to the model when `Session.active_cancel_event` fires while this
command is running — see `_execute`/`_execute_persistent`."""

_CANCEL_STDERR_NOTICE = "SIGINT Triggered by user"
"""Appended to the `stderr` returned to the model on a cancelled call, so the interruption is
visible in the stream the model actually reads, not just in `failure_reason` — see
`_append_cancel_notice`."""

_CANCEL_EXIT_STATUS = 130
"""Synthetic exit status reported for a cancelled call whose child process didn't itself report a
distinct non-zero exit -- the conventional `128 + SIGINT` signal-death code (see
`klorb.tools.bash._decode_exit`), used unconditionally rather than left `None`/`0` so the model
always sees an unambiguous non-zero result for a cancelled call."""


def _append_cancel_notice(stderr_text: str) -> str:
    """Append `_CANCEL_STDERR_NOTICE` to `stderr_text` on its own line, so a cancelled call's
    stderr always carries an explicit, model-visible marker of what happened — used by both
    `_execute` and `_execute_persistent`."""
    if stderr_text and not stderr_text.endswith("\n"):
        stderr_text += "\n"
    return stderr_text + _CANCEL_STDERR_NOTICE


_PWD_TIMEOUT_SECONDS = 10.0
"""Timeout for the short internal round-trips `PersistentShell` runs on its own behalf — the
`pwd` after every command (`_refresh_cwd`), and the `jobs -p`/`export -p` probes a
reconcile-on-grow rebuild uses (`has_live_jobs`/`export_state`). Deliberately short and
independent of `tools.bash.timeout`, since none of these ever legitimately takes long."""


def _pump_lines(stream: IO[str], stream_name: str, sink: "queue.Queue[tuple[str, str | None]]") -> None:
    """Read `stream` line by line, putting `(stream_name, line)` onto `sink` for each one, then
    `(stream_name, None)` once `stream` hits EOF (the process exited or closed this fd) — the
    background pump-thread half of `PersistentShell`'s command-boundary detection, mirroring the
    existing pump-thread pattern used elsewhere for shelled-out children (see
    `klorb.tui.shell.UserShellCommand`)."""
    for line in iter(stream.readline, ""):
        sink.put((stream_name, line))
    sink.put((stream_name, None))
    stream.close()


@dataclass
class PersistentCommandResult:
    """One `shell_lifetime="session"`/`"new"` command's outcome, returned by
    `PersistentShell.run_command`."""

    stdout: str
    stderr: str
    exit_status: int | None
    """`None` if the shell died (EOF) or was killed before an exit status was observed."""
    terminal_alive: bool
    terminal_cwd: str | None
    """The shell's cwd after this command (via a `pwd` round-trip), or `None` if `terminal_alive`
    is `False`."""
    timed_out: bool
    cancelled: bool
    """Whether the caller's `cancel_event` (a Ctrl+C/Escape interrupt — see
    `Session.active_cancel_event`) fired while this command was running, distinct from
    `timed_out` — see `PersistentShell._run_raw`."""


class PersistentShell:
    """A single, long-lived plain `bash` process (no `-i`, no `--rcfile`) reused across `Bash`
    calls within one `Session`, for `shell_lifetime="session"`/`"new"` (see docs/specs/bash-tool-
    and-command-permissions.md's "Session-scoped terminals" section and
    docs/plans/archive/005-session-scoped-bash-terminals.md).

    Deliberately **not** launched with the one-shot path's `-i` (interactive): confirmed
    empirically that an interactive bash reading a continuous stream of commands from a pipe (as
    opposed to running a single `-c "<command>"`) writes a `PS1` prompt to stderr before every
    read, corrupting the sentinel-delimited stderr stream with prompt text interleaved into it —
    a problem the one-shot path never hits, since a `-c` invocation never enters bash's
    interactive read loop at all. `_spawn_persistent_shell` (`klorb.tools.bash.BashTool`) instead
    launches a plain, non-interactive `bash` and runs an explicit bootstrap command (dummying
    `$PS1` just long enough to satisfy the `[ -z "$PS1" ] && return` guard most real `.bashrc`
    files start with, `source`-ing the rcfile, then unsetting `PS1`/`PS2` again) through the
    ordinary `run_command` channel as this shell's first command — see `BashTool._spawn_persistent_
    shell` and docs/adrs/bash-env-uses-clearenv-plus-shareenv-setenv-plus-forced-rcfile.md for why
    the guard clause needs `$PS1` set at all. A non-interactive bash never prints prompts, so
    nothing needs to be stripped from its stderr the way `_strip_bash_shell_noise` strips the
    one-shot path's fixed `-i` startup lines.

    Unlike the one-shot `bash -c "<command>"` path, this shell never exits between commands, so
    there is no process-exit signal to wait on: each command is followed by a fresh, random
    sentinel token (`uuid4().hex`, not a fixed string, so it can't collide with the command's own
    output) written to the shell's own stdin, and two background reader threads
    (`_pump_lines`, one per stream) watch for a matching `__KLORB_DONE_<token>__` line to know the
    command has finished. `run_command` is not safe to call concurrently from multiple threads —
    nothing in this codebase does that today, since `Session._run_tool_calls` dispatches every
    tool call in a turn's round sequentially (see the plan's "Cleanup and lifecycle" section).
    """

    def __init__(
        self, argv: list[str], env: dict[str, str], cwd: str,
        sandbox_snapshot: "frozenset[Path] | None" = None,
    ) -> None:
        self.process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd, start_new_session=True, text=True, bufsize=1)
        self.cwd: str | None = cwd
        self.sandbox_snapshot = sandbox_snapshot
        """The `klorb.sandbox.allowed_dir_snapshot()` this shell's live `bwrap` mount namespace
        was launched with, or `None` when the shell runs unsandboxed. `BashTool._execute_persistent`
        compares it against the session's current snapshot before each command: a `bwrap` mount
        namespace is frozen at launch, so if the session's allowed-directory set has *grown* (an
        interactive grant added an `allow`) the sandbox is stale and must be rebuilt to see the
        new directory — see docs/adrs/rebuild-persistent-sandbox-when-grants-grow.md. `None`
        skips that check entirely (nothing to go stale without a mount namespace)."""
        self.alive = True
        self._queue: "queue.Queue[tuple[str, str | None]]" = queue.Queue()
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        self._stdout_thread = threading.Thread(
            target=_pump_lines, args=(self.process.stdout, "stdout", self._queue), daemon=True)
        self._stderr_thread = threading.Thread(
            target=_pump_lines, args=(self.process.stderr, "stderr", self._queue), daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        atexit.register(self.kill)

    def _write_stdin(self, text: str) -> bool:
        """Write `text` to the shell's stdin, returning whether it succeeded. A failure (the
        shell's stdin is already closed, e.g. because the process died) marks this shell dead
        rather than raising, since that's a normal, expected way to discover a dead shell."""
        if self.process.stdin is None:
            self._mark_dead()
            return False
        try:
            self.process.stdin.write(text)
            self.process.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            self._mark_dead()
            return False

    def run_command(
        self, command: str, timeout_seconds: float,
        cancel_event: threading.Event | None = None,
    ) -> PersistentCommandResult:
        """Run `command` in this shell and return its result, including a refreshed
        `terminal_cwd` (a `pwd` round-trip run immediately afterward, via `_refresh_cwd`) when
        the shell is still alive and the command didn't time out or get cancelled.

        `cancel_event`, if given, is polled while the command runs (see `_run_raw`) so a
        Ctrl+C/Escape interrupt (`Session.active_cancel_event`) reaches this specific running
        command immediately rather than only at the next tool-call boundary. Only the outermost,
        model-issued command should pass one — the internal bootstrap/`pwd`/`jobs -p`/`export -p`
        round-trips below never do, since they're harness bookkeeping, not the thing a user's
        interrupt is aimed at."""
        stdout, stderr, exit_status, timed_out, cancelled = self._run_raw(
            command, timeout_seconds, cancel_event)
        cwd = self._refresh_cwd() if self.alive and not timed_out and not cancelled else self.cwd
        return PersistentCommandResult(
            stdout=stdout, stderr=stderr, exit_status=exit_status, terminal_alive=self.alive,
            terminal_cwd=cwd if self.alive else None, timed_out=timed_out, cancelled=cancelled)

    def _refresh_cwd(self) -> str | None:
        stdout, _stderr, exit_status, timed_out, _cancelled = self._run_raw("pwd", _PWD_TIMEOUT_SECONDS)
        if not self.alive or timed_out or exit_status != 0:
            return self.cwd
        self.cwd = stdout.strip()
        return self.cwd

    def has_live_jobs(self) -> bool:
        """Whether this shell currently has any background jobs (`jobs -p` prints a non-empty
        list) — the probe `BashTool`'s reconcile-on-grow uses before rebuilding a stale sandbox,
        so a rebuild never silently kills background work the model deliberately started (see
        `BashTool._execute_persistent`). A dead or timed-out probe conservatively reports `False`
        (there's no live shell whose jobs a rebuild could destroy). Cannot see already-`disown`'d
        processes — the same acknowledged gap `jobs` itself has."""
        stdout, _stderr, _exit_status, timed_out, _cancelled = self._run_raw(
            "jobs -p", _PWD_TIMEOUT_SECONDS)
        if not self.alive or timed_out:
            return False
        return bool(stdout.strip())

    def export_state(self) -> str:
        """This shell's exported environment as re-executable `declare -x` statements
        (`export -p`), replayed into a rebuilt shell so cwd + exported env survive a
        reconcile-on-grow sandbox rebuild — the load-bearing carryable state (see
        `BashTool._rebuild_persistent_shell` and
        docs/adrs/rebuild-persistent-sandbox-when-grants-grow.md). Returns `""` if the shell died
        or the probe failed, so a rebuild just proceeds without a replay rather than raising."""
        stdout, _stderr, exit_status, timed_out, _cancelled = self._run_raw(
            "export -p", _PWD_TIMEOUT_SECONDS)
        if not self.alive or timed_out or exit_status != 0:
            return ""
        return stdout

    def _run_raw(
        self, command: str, timeout_seconds: float,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, str, int | None, bool, bool]:
        """Run `command` to completion (or timeout/cancellation/death) and return raw
        `(stdout, stderr, exit_status, timed_out, cancelled)` — no cwd refresh, so `_refresh_cwd`
        can call this itself without recursing into another cwd refresh.

        See the module-level `PersistentShell` docstring for the sentinel-token protocol this
        implements. On timeout, or on `cancel_event` becoming set (a Ctrl+C/Escape interrupt):
        `SIGINT` is sent to the shell's whole process group first (the same delivery `Ctrl-C`
        would use), and the sentinel wait continues for `_TIMEOUT_GRACE_SECONDS` longer. Because
        this shell runs non-interactively (no `-i` — see the class docstring), its own default
        `SIGINT` disposition is to terminate, not survive it the way an interactive shell would:
        a plain, non-trapping command's `SIGINT` typically ends the whole shell promptly, well
        within the grace period, rather than leaving it alive for a next call. Only a command
        (or a shell) that explicitly makes itself immune to `SIGINT` consumes the full grace
        period before being escalated to an unconditional `SIGKILL` on the whole process group —
        there is no way to kill only the stuck command without a pty/job-control layer this
        doesn't build. A cancellation is reported as `cancelled=True` regardless of which of
        these ways it actually ends, since either way the user asked for it to stop.
        """
        if not self.alive:
            return "", "", None, False, False

        token = uuid.uuid4().hex
        stdout_done_re = re.compile(rf"^__KLORB_DONE_{token}__ (-?\d+)\s*\Z")
        stderr_done_line = f"__KLORB_DONE_{token}__"
        script = (
            f"{command}\n"
            f"__klorb_ec=$?\n"
            f'printf \'\\n%s %d\\n\' "__KLORB_DONE_{token}__" "$__klorb_ec"\n'
            f'printf \'\\n%s\\n\' "__KLORB_DONE_{token}__" >&2\n'
        )
        if not self._write_stdin(script):
            return "", "", None, False, False

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        exit_status: int | None = None
        stdout_done = False
        stderr_done = False
        stderr_matched = False
        eof = False
        # `None` until `SIGINT` is sent, then fixed as whichever of the two reasons triggered it
        # first -- `"timeout"` (the caller's own `timeout_seconds` elapsed) or `"cancel"`
        # (`cancel_event` fired) -- so a grace-period `SIGKILL` escalation and the final
        # `timed_out`/`cancelled` verdict both know which one actually happened.
        sigint_reason: Literal["timeout", "cancel"] | None = None
        deadline = time.monotonic() + timeout_seconds

        while not (stdout_done and stderr_done):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if sigint_reason is None:
                    self._send_signal(signal.SIGINT)
                    sigint_reason = "timeout"
                    deadline = time.monotonic() + _TIMEOUT_GRACE_SECONDS
                    continue
                self._kill_process_group()
                eof = True
                break
            if (
                sigint_reason is None and cancel_event is not None and cancel_event.is_set()
            ):
                self._send_signal(signal.SIGINT)
                sigint_reason = "cancel"
                deadline = time.monotonic() + _TIMEOUT_GRACE_SECONDS
                continue
            try:
                stream_name, line = self._queue.get(timeout=min(remaining, _CANCEL_POLL_SECONDS))
            except queue.Empty:
                continue
            if line is None:
                eof = True
                if stream_name == "stdout":
                    stdout_done = True
                else:
                    stderr_done = True
                continue
            if stream_name == "stdout":
                match = stdout_done_re.match(line.rstrip("\n"))
                if match is not None:
                    exit_status = int(match.group(1))
                    stdout_done = True
                    continue
                stdout_lines.append(line)
            else:
                if line.rstrip("\n") == stderr_done_line:
                    stderr_done = True
                    stderr_matched = True
                    continue
                stderr_lines.append(line)

        if eof:
            self._mark_dead()
        # If the sentinel showed up before the grace period ran out, the command responded to
        # SIGINT (or finished on its own in the meantime) and the shell is still usable -- so
        # `exit_status is not None` means it was never really a timeout, whichever reason sent
        # the SIGINT. A cancellation still reports `cancelled=True` either way (see docstring
        # above): the user asked for it to stop regardless of how cleanly that happened.
        timed_out = sigint_reason == "timeout" and exit_status is None
        cancelled = sigint_reason == "cancel"

        # Each `printf` in `script` above starts with a literal '\n' so the DONE marker always
        # lands on its own line even if the command's own last line had no trailing newline --
        # but that means one synthetic trailing '\n' is unconditionally present right before the
        # marker whenever it was actually reached. Strip exactly that one character (not a whole
        # "line", since a command with no trailing newline of its own merges it onto the tail of
        # its last real line rather than producing a separate blank one) so a captured stream
        # matches what the command actually wrote, not what this sentinel protocol added to it.
        stdout_text = "".join(stdout_lines)
        if exit_status is not None and stdout_text.endswith("\n"):
            stdout_text = stdout_text[:-1]
        stderr_text = "".join(stderr_lines)
        if stderr_matched and stderr_text.endswith("\n"):
            stderr_text = stderr_text[:-1]

        return stdout_text, stderr_text, exit_status, timed_out, cancelled

    def _send_signal(self, sig: int) -> None:
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            self._mark_dead()

    def _kill_process_group(self) -> None:
        try:
            os.killpg(self.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        self._mark_dead()

    def _mark_dead(self) -> None:
        if not self.alive:
            return
        self.alive = False
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass

    def kill(self) -> None:
        """Unconditionally end this shell: `SIGKILL` its whole process group and mark it dead.
        Safe to call more than once, or on an already-dead shell (a no-op past the first call) --
        registered directly against `atexit` in `__init__`, and also called from
        `BashTool._execute_persistent` (`shell_lifetime="new"`) and `Session.close()` (via the
        teardown callback `BashTool` registers)."""
        if not self.alive:
            return
        self._kill_process_group()


def _standing_interjection_provider(shell: PersistentShell) -> Callable[[], str | None]:
    """Build the `Session.register_standing_interjection` provider for a live persistent shell:
    a message reminding the model it has one open (and how to keep using or close it) for as long
    as `shell.alive`, `None` forever after it dies -- see docs/adrs/standing-interjections-
    complement-one-shot-for-level-triggered-state.md."""

    def provider() -> str | None:
        if not shell.alive:
            return None
        return (
            f"You have a live session-scoped Bash terminal open (cwd: {shell.cwd}). Use "
            "shell_lifetime=\"session\" to keep using it, or shell_lifetime=\"new\" to close it "
            "and start fresh."
        )

    return provider


def build_bash_env(session_config: SessionConfig, bash_command: str) -> dict[str, str]:
    """Build the environment `BashTool` runs a command with: `HOME`/`USER` are always shared
    from the klorb process's own environment, `WORKSPACE_ROOT` is always set to the resolved
    workspace root and `SHELL`/`BASH` are always set to `bash_command` (unconditionally — not
    gated on any config, and not the klorb process's own `$SHELL`/`$BASH` if any — this is the
    actual bash binary the command runs under, per `ProcessConfig.bash_command`), followed by
    every `session_config.share_env` name that's actually set in the klorb process's environment,
    followed by `session_config.set_env`'s overrides (applied last, so they shadow a shared or
    always-set value for the same name). Everything else in the klorb process's own environment
    is left out. When a `bwrap` sandbox is in use this same dict is re-applied inside it via
    `--clearenv` + `--setenv` (see `klorb.sandbox.build_bwrap_argv`); on the unsandboxed fallback
    path there's no `--clearenv` to lean on, so `subprocess.Popen(..., env=...)` receives exactly
    this dict, not `None` (which would inherit the entire parent environment), keeping the
    least-privilege intent intact regardless of which path runs.
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
    boundary — tailored by `klorb.sandbox.detect_bwrap_unavailable_reason()`: a missing `bwrap`
    binary (fixable with `apt-get install bubblewrap`) versus a kernel/container policy that
    forbids unprivileged user namespaces (common inside Docker/cloud-agent environments; fixed by
    reconfiguring the host, not by installing anything). Only reached when `bwrap_available()` is
    `False` — see `_maybe_sandbox_notice`."""
    if detect_bwrap_unavailable_reason() == "missing_binary":
        detail = (
            "Sandbox layer unavailable; cannot launch a sandboxed shell. "
            "Run `sudo apt-get install bubblewrap`, then restart klorb.")
    else:
        detail = (
            "Sandbox layer unavailable: this environment does not permit unprivileged "
            "sandboxing (nested container or restrictive kernel policy).")
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
    is evaluated against `readFiles`/`writeFiles` and `readDirs`/`writeDirs` via the same
    `resolve_and_evaluate_write`/`resolve_and_evaluate_read` the file tools use. The overall
    verdict is the strictest of all
    of these (`klorb.permissions.table.stricter_verdict`) — never more permissive than any single
    contributor — enforced through the same `raise_if_not_allowed()` seam every other tool uses.

    Once permitted, the command runs via `bash --rcfile ${HOME}/.bashrc -i -c "unset PS1; unset
    PS2; <command>"`, wrapped in a `bwrap` sandbox (see `klorb.sandbox.build_bwrap_argv`) whenever
    one can actually be created on this host (`klorb.sandbox.bwrap_available`). Where it can't (no
    `bwrap` binary, or a kernel/container policy forbidding unprivileged user namespaces — common
    inside Docker/cloud-agent environments) the command falls back to unsandboxed execution, with
    a one-time notice the first time that happens in a given session (see `_sandbox_notice`) — the
    permission checks above are enforced identically either way.

    `shell_lifetime` is optional and selects how long the underlying shell process lives; a
    missing key, a `None`, or an empty string all default to `"command"`. `"command"` spawns a
    fresh, non-persistent shell that exits when the command finishes (no state carries over to
    the next call); `"session"` reuses the one live persistent shell for this `Session` if one
    exists, or creates it otherwise; `"new"` kills any existing persistent shell first, then
    creates a fresh one that becomes the persistent shell for subsequent `"session"` calls. At
    most one persistent shell exists per `Session` at a time — see `PersistentShell` and
    docs/specs/bash-tool-and-command-permissions.md's "Session-scoped terminals" section.

    `intent` is a required, model-supplied short statement of what `command` is trying to
    accomplish — shown alongside the command in the approval dialog and the history scroll (see
    `summary()` and `klorb.tui.panels.permission_ask_panel.PermissionAskPanel`), and passed to
    `klorb.permissions.risk_classifier` so a command that looks deceptively different from its
    own stated intent scores as more risky. See docs/specs/bash-tool-and-command-permissions.md's
    "Agent-stated intent" section.
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
            "stderr_file if the output is large), and runtime in seconds. shell_lifetime "
            "is optional and selects the shell's lifetime; omit it (or pass null/empty) for the "
            "default \"command\" behavior: \"command\" starts a fresh, "
            "non-persistent shell that exits when the command finishes, with no state (cwd, "
            "env vars, background jobs) carried over to the next call; \"session\" reuses this "
            "session's one persistent shell if it has one, or creates it otherwise, so cwd/env/"
            "background-job state persists across calls; \"new\" closes any existing "
            "persistent shell and starts a fresh one that becomes the persistent shell for "
            "subsequent \"session\" calls. \"session\"/\"new\" responses also include "
            "terminal_alive (whether the persistent shell is still usable) and terminal_cwd "
            "(its current directory, or null if terminal_alive is false). intent is a short, "
            "plain-English statement of what the command is trying to accomplish, shown to the "
            "user alongside the command itself when it needs approval or appears in history. "
            "For example: 'list all files in foobar directory.' "
            "State it accurately -- a command that does something other than what its own "
            "intent describes is scored as more risky."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                },
                "intent": {
                    "type": "string",
                    "description": (
                        "A short, human-readable statement of what this command is trying to "
                        "accomplish."
                    ),
                },
                "shell_lifetime": {
                    "type": "string",
                    "enum": ["command", "session", "new"],
                    "description": (
                        "Optional; omit (or pass null/empty) for a fresh command-scoped shell. "
                        "\"command\": fresh, non-persistent shell for this call only. "
                        "\"session\": reuse (or create) this session's one persistent shell. "
                        "\"new\": close any existing persistent shell and start a fresh one."
                    ),
                },
            },
            "required": ["command", "intent"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            command = args["command"]
        except KeyError:
            raise ValueError("Missing required argument: 'command'. Include the bash command to execute.")

        try:
            intent = args["intent"]
        except KeyError:
            try:
                # Some models really insist on sending a 'description' instead of 'intent'. Fine.
                intent = args["description"]
            except KeyError:
                raise ValueError("Missing required argument: 'intent'. Include a human-readable " +
                                "description of the command's purpose.")

        # shell_lifetime is optional: a missing key, a null, or an empty string all mean "run this
        # command in a fresh, command-scoped shell" -- exactly the same as an explicit "command".
        shell_lifetime = args.get("shell_lifetime") or "command"

        if shell_lifetime not in ("command", "session", "new"):
            raise ValueError(
                f"shell_lifetime must be one of 'command'/'session'/'new', got {shell_lifetime!r}")

        if not command.strip():
            raise ValueError("command must not be empty")

        logger.debug("Bash %r (intent=%r, shell_lifetime=%s)", command, intent, shell_lifetime)

        analysis = parse_command(command, self._shfmt_command)
        verdict, ask_items = self._classify(analysis, command, intent)
        if verdict == "deny":
            raise PermissionError(f"Permission denied: run shell command: {command}")
        elif verdict == "ask":
            raise MultiPermissionAskRequired(
                f"Permission requires confirmation: run shell command: {command}", items=ask_items)

        if shell_lifetime == "command":
            return self._execute(command)
        return self._execute_persistent(command, shell_lifetime)

    def _classify(
        self, analysis: BashCommandAnalysis, command: str, intent: str,
    ) -> tuple[Verdict, list[PermissionAskItem]]:
        """Evaluate every simple command's `CommandPermissionsTable` verdict, every redirection
        target's directory-access verdict, and every reason the walker itself forced an "ask".

        A single `"deny"` anywhere short-circuits: the whole command is denied outright, with no
        `PermissionAskItem`s collected at all (nothing else needs asking about once the call is
        already going to be refused). Otherwise, every individual `"ask"` contributor becomes its
        own `PermissionAskItem` — not just the strictest one — so `Session` can ask about each
        in series (see `MultiPermissionAskRequired`) rather than collapsing a compound command
        with several independent concerns into one opaque prompt. `"allow"` (no deny, no ask
        items at all) means the whole command is permitted to run as-is.

        `command` (the model's original, unparsed command string) is set as every collected
        item's `command_text` — `analysis` alone has no notion of the raw command text, and a UI
        (`klorb.tui.panels.permission_ask_panel.PermissionAskPanel`) needs it to show what's actually
        being run, on top of each item's own specific `resource_description` detail (see
        `PermissionAskItem.command_text`). Every collected item also gets the same
        `is_compound` (`analysis.command_count > 1` — every executable node the walker visited,
        counted regardless of whether its own argv was fully literal, so a `for`/`if`/`case` body
        command with a non-literal argument still counts even though it never reaches
        `analysis.simple_commands`; see `BashCommandAnalysis.command_count`), so a UI can tell a
        decision about one simple command (`resource_description`) apart from the full command
        line it belongs to, even when that full line is short enough to display without
        truncation (see `PermissionAskItem.is_compound`). Each item also gets its own
        `item_command_text` — `analysis`'s `SimpleCommand`/`ForcedAskReason`/`RedirectTarget`
        each carry their own `source_text`, the exact original source of the one statement this
        particular item is actually about, distinct from `command_text`'s full raw command — so
        a UI can show *this item's own command* as the prominent preview, not the whole compound
        line every other item in the same batch would show identically (see
        `PermissionAskItem.item_command_text`). `intent` (the model's own required argument
        describing what the whole command is trying to accomplish) is set identically on every
        collected item too, the same way `command_text` is, so a UI and the risk classifier can
        both see it (see `PermissionAskItem.intent`).

        `context.permission_override`, when set, lets a previously-approved-once resource skip
        straight past its check on this one retried call (see `PermissionOverride`): a simple
        command whose exact argv is in `override.commands` never reaches `command_table`, and a
        structural item's `resource_description` (the walker's own reason text — deterministic
        across a retry, since `parse_command` is pure and `command` is identical) being in
        `override.reasons` drops it rather than turning it into another `PermissionAskItem`.
        Redirection targets don't need an analogous check here — they're `Path`s, already covered
        by `override.paths` inside `evaluate_write`/`resolve_and_evaluate_read` themselves (see
        `_evaluate_redirect`).
        """
        command_table = CommandPermissionsTable(self.context.session_config.command_rules)
        override = self.context.permission_override
        ask_items: list[PermissionAskItem] = []
        denied = False
        is_compound = analysis.command_count > 1

        for simple_command in analysis.simple_commands:
            argv = simple_command.argv
            if override is not None and tuple(argv) in override.commands:
                continue
            verdict = command_table.evaluate(argv)
            if verdict == "deny":
                denied = True
            elif verdict is None or verdict == "ask":
                ask_items.append(PermissionAskItem(
                    f"run command: {' '.join(argv)}", command=argv, command_text=command,
                    is_compound=is_compound, item_command_text=simple_command.source_text,
                    intent=intent))

        for forced_reason in analysis.forced_ask_reasons:
            if override is not None and forced_reason.reason in override.reasons:
                continue
            ask_items.append(PermissionAskItem(
                forced_reason.reason, command_text=command, is_compound=is_compound,
                item_command_text=forced_reason.source_text, intent=intent))

        for redirect in analysis.redirects:
            redirect_verdict, path, is_write = self._evaluate_redirect(redirect)
            if redirect_verdict == "deny":
                denied = True
            elif redirect_verdict == "ask":
                action = "write to" if is_write else "read"
                ask_items.append(PermissionAskItem(
                    f"{action} {path}", path=path, is_write=is_write, command_text=command,
                    is_compound=is_compound, item_command_text=redirect.source_text,
                    intent=intent))

        if denied:
            logger.info("Bash command denied outright: %s", analysis)
            return "deny", []
        if ask_items:
            logger.info("Bash command has %d item(s) needing confirmation", len(ask_items))
            return "ask", ask_items
        return "allow", []

    def _evaluate_redirect(self, redirect: RedirectTarget) -> tuple[Verdict, Path, bool]:
        if redirect.direction == "write":
            path, verdict = resolve_and_evaluate_write(self.context, redirect.target)
            return verdict, path, True
        path, verdict = resolve_and_evaluate_read(self.context, redirect.target)
        return verdict, path, False

    def _compute_sandbox_dirs(self, home: Path) -> SandboxDirs:
        """The current `SandboxDirs` bind sets for this session's permission tables — recomputed
        on demand (not cached) so a mid-session grant that widens `readDirs`/`writeDirs` is
        reflected the next time a one-shot command is sandboxed, and so the persistent shell's
        reconcile-on-grow check (`_execute_persistent`) sees the up-to-date set."""
        config = self.context.session_config
        return compute_sandbox_dirs(
            workspace_root=config.workspace.path.resolve(),
            home=home,
            trusted=config.workspace.trusted,
            read_dirs=config.read_dirs,
            write_dirs=config.write_dirs,
            read_files=config.read_files,
            write_files=config.write_files,
            approved_scopes=config.approved_scopes)

    def _bwrap_prefix(self, env: dict[str, str], home: Path) -> list[str]:
        """The `bwrap ... --` argv prefix for this session, to prepend to a shell invocation.
        Only meaningful when `bwrap_available()`; callers gate on that themselves."""
        return build_bwrap_argv(
            workspace_root=self.context.session_config.workspace.path.resolve(),
            home=home, env=env, dirs=self._compute_sandbox_dirs(home),
            path_dirs=path_dirs_from_env())

    def _active_cancel_event(self) -> threading.Event | None:
        """The in-flight turn's cancellation signal (`Session.active_cancel_event` — set for as
        long as `Session._dispatch_turn` is running, which includes this very call, since a
        `Tool.apply()` runs synchronously on `_dispatch_turn`'s own thread), or `None` when this
        `BashTool` wasn't built from a real `Session` (e.g. a caller that constructs a
        `ToolSetupContext` directly, as most unit tests do) — in which case a running command
        simply isn't cancellable. Polled by `_execute`/`PersistentShell._run_raw` so a Ctrl+C/
        Escape interrupt reaches a running command immediately via `SIGINT`, rather than only at
        the next tool-call/round boundary `Session` itself checks."""
        session = self.context.session
        return session.active_cancel_event if session is not None else None

    def _execute(self, command: str) -> dict[str, Any]:
        workspace_root = self.context.session_config.workspace.path.resolve()
        env = build_bash_env(self.context.session_config, self._bash_command)
        home = env.get("HOME", str(Path.home()))
        rcfile = str(Path(home) / ".bashrc")
        full_command = f"unset PS1; unset PS2; {command}"
        inner_argv = [self._bash_command, "--rcfile", rcfile, "-i", "-c", full_command]
        # bwrap wraps the same inner invocation when a sandbox can actually be created here;
        # otherwise the command runs unsandboxed (with a one-time notice — see
        # _maybe_sandbox_notice), permission classification unaffected either way.
        argv = self._bwrap_prefix(env, Path(home)) + inner_argv if bwrap_available() else inner_argv

        tmp_dir = Path(tempfile.mkdtemp(prefix=_TMP_DIR_PREFIX))
        # Registered immediately after creation, before anything else can raise, so this
        # directory is always swept on process exit even if klorb dies mid-command.
        atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
        stdout_path = tmp_dir / "stdout"
        stderr_path = tmp_dir / "stderr"
        for path in (stdout_path, stderr_path):
            path.touch()
            os.chmod(path, 0o600)

        sandbox_notice = self._maybe_sandbox_notice()

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
            cancelled = False
            cancel_event = self._active_cancel_event()
            deadline = time.monotonic() + self._timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # Kill the whole process group, not just `process`'s own pid: a background job
                    # the command (or its sourced ~/.bashrc) started -- and that survived bash's own
                    # tail-call-exec optimization, so it's a distinct process from `process.pid` --
                    # would otherwise outlive the timeout as an orphan. Safe because
                    # start_new_session=True above made this child its own process group leader.
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    timed_out = True
                    break
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                try:
                    process.wait(timeout=min(remaining, _CANCEL_POLL_SECONDS))
                    break
                except subprocess.TimeoutExpired:
                    continue

            if cancelled:
                # Same SIGINT-first, grace-period-then-SIGKILL escalation
                # `PersistentShell._run_raw` uses for its own cancellation path: give the command
                # a chance to exit on its own in response to SIGINT before resorting to an
                # unconditional SIGKILL.
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=_TIMEOUT_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        runtime = time.monotonic() - start

        if cancelled:
            success = False
            failure_reason: str | None = _CANCEL_FAILURE_REASON
            exit_status = (
                process.returncode if process.returncode not in (0, None) else _CANCEL_EXIT_STATUS)
        elif timed_out:
            success = False
            failure_reason = f"Command timed out at {self._timeout_seconds} seconds"
            exit_status = process.returncode if process.returncode is not None else -1
        else:
            exit_status = process.returncode
            success, failure_reason = _decode_exit(exit_status)

        stderr_content = _strip_bash_shell_noise(
            stderr_path.read_text(encoding="utf-8", errors="replace"))
        if cancelled:
            stderr_content = _append_cancel_notice(stderr_content)
        stderr_path.write_text(stderr_content, encoding="utf-8")

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

    def _maybe_sandbox_notice(self) -> str | None:
        """Return `_sandbox_notice()` the first time a command actually runs unsandboxed for the
        current `Session` (tracked via `session.tool_state["Bash"]["sandbox_warned"]`), or every
        such call if there's no `Session` to dedupe against — shared by `_execute` and
        `_execute_persistent`. Returns `None` whenever `bwrap_available()` (the command ran inside
        a real sandbox, so there's nothing to warn about)."""
        if bwrap_available():
            return None
        if self.context.session is None:
            # No Session to dedupe against (e.g. a caller that built a ToolSetupContext
            # directly) -- show it every call rather than silently dropping it.
            return _sandbox_notice()
        bash_state = self.context.session.tool_state.setdefault(_TOOL_STATE_KEY, {})
        if not bash_state.get(_SANDBOX_WARNED_KEY, False):
            bash_state[_SANDBOX_WARNED_KEY] = True
            return _sandbox_notice()
        return None

    def _spawn_persistent_shell(
        self, restore_env: str | None = None, restore_cwd: str | None = None,
    ) -> PersistentShell:
        workspace_root = self.context.session_config.workspace.path.resolve()
        env = build_bash_env(self.context.session_config, self._bash_command)
        home = env.get("HOME", str(Path.home()))
        rcfile = str(Path(home) / ".bashrc")
        # Same bwrap prefix as the one-shot path, computed once at spawn (a bwrap mount namespace
        # is fixed for the life of the process, so a persistent shell can't recompute it per
        # command -- see reconcile-on-grow in _execute_persistent). The snapshot records the
        # allowed-dir set this sandbox was launched with, so that reconcile can detect a later
        # grant that widened it. Unsandboxed hosts get argv=[bash] and snapshot=None.
        if bwrap_available():
            argv = self._bwrap_prefix(env, Path(home)) + [self._bash_command]
            snapshot: frozenset[Path] | None = allowed_dir_snapshot(
                self._compute_sandbox_dirs(Path(home)))
        else:
            argv = [self._bash_command]
            snapshot = None
        shell = PersistentShell(argv, env, str(workspace_root), sandbox_snapshot=snapshot)
        # PS1='x' satisfies the `[ -z "$PS1" ] && return` guard most real .bashrc files start
        # with (see PersistentShell's docstring and docs/adrs/bash-env-uses-clearenv-plus-
        # shareenv-setenv-plus-forced-rcfile.md) without ever printing a prompt -- this shell was
        # deliberately launched without -i, so nothing reads $PS1 as an actual prompt string.
        # Discarding the result: this is harness bootstrapping, not a model-visible command: a
        # missing/erroring rcfile shouldn't fail shell creation, just leave PATH/toolchain setup
        # up to whatever build_bash_env already put in the environment.
        bootstrap = f'PS1=x\n[ -f "{rcfile}" ] && source "{rcfile}"\nunset PS1 PS2\n'
        shell.run_command(bootstrap, self._timeout_seconds)
        # On a reconcile-on-grow rebuild, replay the prior shell's exported environment and cwd
        # into the fresh one (after rc-file bootstrap, so accumulated values win) -- see
        # _rebuild_persistent_shell. Background jobs are unavoidably not carried over.
        if restore_env:
            shell.run_command(restore_env, self._timeout_seconds)
        if restore_cwd:
            shell.run_command(f"cd {shlex.quote(restore_cwd)}", self._timeout_seconds)
        return shell

    def _rebuild_persistent_shell(
        self, old: PersistentShell, bash_state: dict[str, Any],
    ) -> PersistentShell:
        """Replace a stale sandboxed persistent shell with a fresh one launched against the
        current (wider) permission tables, replaying the old shell's exported env + cwd, then
        `SIGKILL` the old sandbox. Only called once `has_live_jobs()` has confirmed no background
        work would be lost — see `_execute_persistent` and
        docs/adrs/rebuild-persistent-sandbox-only-when-no-live-jobs.md."""
        restore_env = old.export_state()
        restore_cwd = old.cwd
        new_shell = self._spawn_persistent_shell(restore_env=restore_env, restore_cwd=restore_cwd)
        old.kill()
        bash_state[_PERSISTENT_SHELL_KEY] = new_shell
        return new_shell

    def _execute_persistent(self, command: str, shell_lifetime: str) -> dict[str, Any]:
        session = self.context.session
        if session is None:
            raise ValueError(
                f"shell_lifetime={shell_lifetime!r} requires a Session to keep the persistent "
                "shell in between calls; use shell_lifetime='command' without a Session.")

        bash_state = session.tool_state.setdefault(_TOOL_STATE_KEY, {})
        shell: PersistentShell | None = bash_state.get(_PERSISTENT_SHELL_KEY)
        if shell is not None and not shell.alive:
            shell = None
        if shell_lifetime == "new" and shell is not None:
            shell.kill()
            shell = None
        reused = shell is not None
        if shell is None:
            shell = self._spawn_persistent_shell()
            bash_state[_PERSISTENT_SHELL_KEY] = shell

        sandbox_rebuilt = False
        if reused:
            shell, sandbox_rebuilt, stale_refusal = self._reconcile_sandbox(shell, bash_state)
            if stale_refusal is not None:
                session.register_standing_interjection(
                    _STANDING_INTERJECTION_SUBJECT, _standing_interjection_provider(shell))
                session.register_teardown(_TEARDOWN_SUBJECT, shell.kill)
                return self._sandbox_stale_response(command, shell, stale_refusal)

        session.register_standing_interjection(
            _STANDING_INTERJECTION_SUBJECT, _standing_interjection_provider(shell))
        session.register_teardown(_TEARDOWN_SUBJECT, shell.kill)

        sandbox_notice = self._maybe_sandbox_notice()

        start = time.monotonic()
        result = shell.run_command(command, self._timeout_seconds, self._active_cancel_event())
        runtime = time.monotonic() - start

        if not result.terminal_alive:
            bash_state[_PERSISTENT_SHELL_KEY] = None

        if result.cancelled:
            success = False
            failure_reason: str | None = _CANCEL_FAILURE_REASON
            exit_status: int | None = (
                result.exit_status if result.exit_status not in (0, None) else _CANCEL_EXIT_STATUS)
        elif result.timed_out:
            success = False
            failure_reason = f"Command timed out at {self._timeout_seconds} seconds"
            exit_status = result.exit_status
        elif result.exit_status is None:
            success = False
            failure_reason = "Terminal closed before the command finished"
            exit_status = None
        else:
            exit_status = result.exit_status
            success, failure_reason = _decode_exit(exit_status)

        stderr_for_model = _append_cancel_notice(result.stderr) if result.cancelled else result.stderr
        stdout_text, stdout_file, stderr_text, stderr_file = self._finalize_persistent_output(
            result.stdout, stderr_for_model)

        logger.info(
            "Bash command (shell_lifetime=%s) finished: exit_status=%s success=%s "
            "terminal_alive=%s runtime=%.2fs",
            shell_lifetime, exit_status, success, result.terminal_alive, runtime)

        response: dict[str, Any] = {
            "command": command,
            "exit_status": exit_status,
            "success": success,
            "failure_reason": failure_reason,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "stdout_file": stdout_file,
            "stderr_file": stderr_file,
            "runtime": runtime,
            "terminal_alive": result.terminal_alive,
            "terminal_cwd": result.terminal_cwd,
            "sandbox_rebuilt": sandbox_rebuilt,
        }
        if sandbox_notice is not None:
            response["sandbox_notice"] = sandbox_notice
        return response

    def _reconcile_sandbox(
        self, shell: PersistentShell, bash_state: dict[str, Any],
    ) -> tuple[PersistentShell, bool, str | None]:
        """Reconcile a reused persistent shell's frozen `bwrap` mount namespace with the session's
        current permission tables before a command runs in it. Returns
        `(shell_to_use, sandbox_rebuilt, stale_refusal)`:

        * Unsandboxed shell (`sandbox_snapshot is None`), or the allowed-dir set is unchanged (the
          overwhelmingly common case): the same shell, `False`, `None` — run the command as-is.
        * The allowed-dir set grew (an interactive grant added an `allow` since this sandbox
          launched — see docs/adrs/rebuild-persistent-sandbox-when-grants-grow.md) and the shell
          has no live background jobs: a freshly rebuilt shell, `True`, `None`.
        * It grew but the shell *does* have live background jobs: the original shell (untouched),
          `False`, and a non-`None` refusal string — the caller must not run the command, to avoid
          silently killing that background work; the model is told to opt into a
          `shell_lifetime="new"` respawn instead (see
          docs/adrs/rebuild-persistent-sandbox-only-when-no-live-jobs.md).
        """
        if shell.sandbox_snapshot is None:
            return shell, False, None
        env = build_bash_env(self.context.session_config, self._bash_command)
        home = Path(env.get("HOME", str(Path.home())))
        current = allowed_dir_snapshot(self._compute_sandbox_dirs(home))
        if current == shell.sandbox_snapshot:
            return shell, False, None
        if shell.has_live_jobs():
            return shell, False, (
                "This session-scoped terminal was sandboxed before a directory it now has "
                "permission to access was granted, and its sandbox has live background jobs an "
                "automatic rebuild would kill. Re-run with shell_lifetime=\"new\" to start a "
                "fresh terminal that can see the newly granted directory (this ends the current "
                "terminal and its background jobs), or use shell_lifetime=\"command\" for a "
                "one-off command.")
        return self._rebuild_persistent_shell(shell, bash_state), True, None

    def _sandbox_stale_response(
        self, command: str, shell: PersistentShell, refusal: str,
    ) -> dict[str, Any]:
        """The response for a reconcile-on-grow refusal (live background jobs blocked an automatic
        rebuild — see `_reconcile_sandbox`): the command did not run, the existing terminal is
        left alive and untouched, and `failure_reason` explains how to pick up the new grant."""
        return {
            "command": command,
            "exit_status": None,
            "success": False,
            "failure_reason": refusal,
            "stdout": "",
            "stderr": "",
            "stdout_file": None,
            "stderr_file": None,
            "runtime": 0.0,
            "terminal_alive": True,
            "terminal_cwd": shell.cwd,
            "sandbox_rebuilt": False,
        }

    def _finalize_persistent_output(
        self, stdout: str, stderr: str,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Spill `stdout`/`stderr` (in-memory strings, unlike `_execute`'s already-on-disk
        capture files) to a fresh per-call directory if either exceeds `bash_spill_bytes`,
        mirroring `_execute`'s `stdout`/`stdout_file` (`None`/non-`None`, exactly one of each
        pair) contract."""
        stdout_over = len(stdout.encode("utf-8")) > self._spill_bytes
        stderr_over = len(stderr.encode("utf-8")) > self._spill_bytes
        if not stdout_over and not stderr_over:
            return stdout, None, stderr, None

        tmp_dir = Path(tempfile.mkdtemp(prefix=_TMP_DIR_PREFIX))
        atexit.register(shutil.rmtree, tmp_dir, ignore_errors=True)
        stdout_text, stdout_file = self._spill_text(stdout, tmp_dir / "stdout")
        stderr_text, stderr_file = self._spill_text(stderr, tmp_dir / "stderr")
        self._grant_tmp_dir_read_access(tmp_dir)
        return stdout_text, stdout_file, stderr_text, stderr_file

    def _spill_text(self, text: str, path: Path) -> tuple[str | None, str | None]:
        data = text.encode("utf-8")
        if len(data) <= self._spill_bytes:
            return text, None
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return None, str(path)

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

    def is_success(self, args: dict[str, Any], result: Any, error: str | None) -> bool:
        """A shell command that ran but failed (non-zero exit, timeout, a dead persistent
        terminal, or a reconcile-on-grow refusal) doesn't raise — `apply()` returns a result
        dict carrying `"success": False` (see `_decode_exit` and `_execute_persistent`). So the
        base class's `error is None` heuristic isn't enough here: honor the result's own
        `"success"` flag once no exception was raised.
        """
        if error is not None:
            return False
        return bool(result.get("success")) if isinstance(result, dict) else True

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Prefix the rendered command with the model's own `intent` (when given), so the
        history scroll shows what the command is *for* alongside what it actually runs — e.g.
        `Bash: List all _wait_until call sites in test_tui_repl.py ($ grep -n ...)`."""
        command = args.get("command", "?")
        intent = args.get("intent")
        label = f"{intent}\n$ {command}" if intent else command
        if error is not None:
            return f"Bash: {label} failed: {error}"
        if not isinstance(result, dict):
            return f"Bash: {label}"
        status = "ok" if result.get("success") else f"exit {result.get('exit_status')}"
        return f"Bash: {label} ({status}, {result.get('runtime', 0):.2f}s)"

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
