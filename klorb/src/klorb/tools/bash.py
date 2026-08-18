# © Copyright 2026 Aaron Kimball
"""A Tool that runs a shell command requested by the model, gated by two layers of defense: a
`CommandRules`/`readDirs`/`writeDirs`-driven allow/ask/deny classification of the parsed command,
and an OS-level `bwrap` sandbox boundary around the actual execution, with an unsandboxed
fallback where `bwrap` can't create a sandbox. See
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
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable, Literal

from klorb.permissions.command_access import CommandPermissionsTable
from klorb.permissions.directory_access import DirRules
from klorb.permissions.domain_access import evaluate_domain, parse_domain
from klorb.permissions.resource import (
    BashCommandContext,
    CommandResource,
    DomainResource,
    PathResource,
    StructuralResource,
)
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
from klorb.sandbox.network import SandboxNetworking, start_sandbox_networking
from klorb.session import SessionConfig
from klorb.tools.interruptible_tool import InterruptibleTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import truncate_lines
from klorb.workspace.session_store import session_subdir_path

logger = logging.getLogger(__name__)

_TMP_DIR_PREFIX = "klorb-bash-"

KLORB_ENV_FILE_VAR = "KLORB_ENV_FILE"
"""Env var pointing a bash subprocess at its session's `session_env_file()`."""

_ENV_FILE_BASENAME = "bash_env"
"""Subdirectory name within a session's data directory holding the env file."""


def _shell_single_quote(value: str) -> str:
    """Wrap `value` in single quotes for safe use in a shell `export` statement.

    Interior literal single quotes are escaped via the standard bash idiom
    ``'end'\''start'`` -- end the current single-quoted segment, emit a bare
    escaped ``'``, then open a new single-quoted segment -- so a value like
    ``home's`` becomes ``'home'\''s'``."""
    return "'" + value.replace("'", "'\\''") + "'"


def session_env_file(
    session_id: str, session_config: "SessionConfig | None" = None,
) -> Path:
    """The `KLORB_ENV_FILE_VAR` target for `session_id`:

    ``<session_dir>/bash_env/session.env`` where ``<session_dir>`` is the session's own
    per-project data directory (``session_subdir_path(workspace, session_id)``).
    Created and populated on first use, then left in place for the rest of the
    session -- unlike a one-off temp file, nothing removes it once a subprocess that used it
    exits.

    On first creation the file is populated with ``export`` statements for ``NO_COLOR``, every
    ``session_config.share_env`` name present in the process environment, and every
    ``session_config.set_env`` override.  Values are single-quoted so they are never
    macro-expanded by the sourcing shell.
    """
    workspace = session_config.workspace if session_config is not None else None
    if workspace is not None:
        path = session_subdir_path(workspace, session_id) / _ENV_FILE_BASENAME / "session.env"
    else:
        # No workspace available (shouldn't happen in practice); fall back to a
        # state-dir path so the file is still creatable.
        from klorb.paths import get_klorb_state_dir
        path = get_klorb_state_dir() / _ENV_FILE_BASENAME / session_id / "session.env"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _populate_env_file(path, session_config)
        logger.debug("Created session env file %s", path)
    return path


def _populate_env_file(path: Path, session_config: "SessionConfig | None") -> None:
    """Write the initial contents of a session env file.

    The file contains ``export VAR='value'`` lines for ``NO_COLOR``, the
    ``session_config.share_env`` passthrough names, and ``session_config.set_env``
    overrides (applied last so they shadow a shared value for the same name).
    """
    path.write_text(
        "\n".join(_env_export_lines(session_config)) + "\n", encoding="utf-8")


def _env_export_lines(session_config: "SessionConfig | None") -> list[str]:
    """Shell ``export`` statements for ``NO_COLOR``, ``share_env``, and ``set_env``.

    Used both to populate a session env file on disk and as an inline fallback when the
    env file's directory is read-only."""
    lines: list[str] = []
    lines.append(f"export NO_COLOR={_shell_single_quote('true')}")
    if session_config is not None:
        for name in session_config.share_env:
            if name in os.environ:
                lines.append(f"export {name}={_shell_single_quote(os.environ[name])}")
        for name, value in session_config.set_env.items():
            lines.append(f"export {name}={_shell_single_quote(value)}")
    return lines


_TOOL_STATE_KEY = "Bash"
_SANDBOX_WARNED_KEY = "sandbox_warned"
"""`session.tool_state["Bash"]["sandbox_warned"]`: whether the one-time sandbox-fallback notice
has already been emitted for this session."""

_PERSISTENT_SHELL_KEY = "persistent_shell"
"""`session.tool_state["Bash"]["persistent_shell"]`: the single live `PersistentShell` (or
`None`) `shell_lifetime="session"`/`"new"` calls reuse. At most one persistent shell exists
per `Session` at a time."""

_STANDING_INTERJECTION_SUBJECT = "SessionTerminal"
"""`Session.register_standing_interjection()` subject key `BashTool` registers under whenever it
creates or reuses a persistent shell."""

_TEARDOWN_SUBJECT = "Bash"
"""`Session.register_teardown()` subject key `BashTool` registers under for killing a live
persistent shell when the owning `Session` is closed."""

_TIMEOUT_GRACE_SECONDS = 3.0
"""How long a persistent-shell command is given to finish after `SIGINT` before being escalated
to `SIGKILL` on timeout or cancellation."""

_CANCEL_POLL_SECONDS = 0.1
"""How often the one-shot and persistent-shell wait loops wake up to check
`Session.active_cancel_event` while a command runs, so a Ctrl+C/Escape interrupt is noticed
promptly rather than only once the command's own output arrives or its full `tools.bash.timeout`
elapses."""

_CANCEL_FAILURE_REASON = (
    "Command canceled: the user pressed Ctrl+C/Escape to interrupt this tool call.")
"""`failure_reason` reported to the model when `Session.active_cancel_event` fires while this
command is running."""

_CANCEL_STDERR_NOTICE = "SIGINT Triggered by user"
"""Appended to the `stderr` returned to the model on a cancelled call, so the interruption is
visible in the stream the model actually reads, not just in `failure_reason`."""

_CANCEL_EXIT_STATUS = 130
"""Synthetic exit status reported for a cancelled call whose child process didn't itself report a
distinct non-zero exit."""


def _append_cancel_notice(stderr_text: str) -> str:
    """Append `_CANCEL_STDERR_NOTICE` to `stderr_text` on its own line, so a cancelled call's
    stderr always carries an explicit, model-visible marker of what happened."""
    if stderr_text and not stderr_text.endswith("\n"):
        stderr_text += "\n"
    return stderr_text + _CANCEL_STDERR_NOTICE


_PWD_TIMEOUT_SECONDS = 10.0
"""Timeout for the short internal round-trips `PersistentShell` runs on its own behalf.
Deliberately short and independent of `tools.bash.timeout`, since none of these ever
legitimately takes long."""


def _pump_lines(stream: IO[str], stream_name: str, sink: "queue.Queue[tuple[str, str | None]]") -> None:
    """Read `stream` line by line, putting `(stream_name, line)` onto `sink` for each one, then
    `(stream_name, None)` once `stream` hits EOF."""
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
    """Whether the caller's `cancel_event` fired while this command was running, distinct from
    `timed_out`."""


class PersistentShell:
    """A single, long-lived plain `bash` process reused across `Bash` calls within one
    `Session`, for `shell_lifetime="session"`/`"new"`.

    Each command is followed by a fresh sentinel token written to the shell's stdin, and
    two background reader threads watch for a matching sentinel line to know the command has
    finished.
    """

    def __init__(
        self, argv: list[str], env: dict[str, str], cwd: str,
        sandbox_snapshot: "frozenset[Path] | None" = None,
        networking: SandboxNetworking | None = None,
    ) -> None:
        pass_fds = () if networking is None else (networking.sandbox_fd,)
        self.process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd, start_new_session=True, text=True, bufsize=1, pass_fds=pass_fds)
        self.cwd: str | None = cwd
        self.sandbox_snapshot = sandbox_snapshot
        """The `klorb.sandbox.allowed_dir_snapshot()` this shell's live `bwrap` mount namespace
        was launched with, or `None` when the shell runs unsandboxed. `None` skips the
        reconcile-on-grow check entirely."""
        self.networking = networking
        """This shell's live `SandboxNetworking`, or `None` when the shell is unsandboxed or
        `tools.bash.network.enabled` is `False`."""
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
        """Write `text` to the shell's stdin, returning whether it succeeded. A failure marks
        this shell dead rather than raising, since that's a normal, expected way to discover a
        dead shell."""
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
        `terminal_cwd` when the shell is still alive and the command didn't time out or get
        cancelled.

        `cancel_event`, if given, is polled while the command runs so a Ctrl+C/Escape
        interrupt reaches this specific running command immediately rather than only at the
        next tool-call boundary."""
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
        """Whether this shell currently has any background jobs. A dead or timed-out probe
        conservatively reports `False`."""
        stdout, _stderr, _exit_status, timed_out, _cancelled = self._run_raw(
            "jobs -p", _PWD_TIMEOUT_SECONDS)
        if not self.alive or timed_out:
            return False
        return bool(stdout.strip())

    def export_state(self) -> str:
        """This shell's exported environment as re-executable `declare -x` statements, replayed
        into a rebuilt shell so cwd + exported env survive a reconcile-on-grow sandbox rebuild.
        Returns `""` if the shell died or the probe failed, so a rebuild just proceeds without
        a replay rather than raising."""
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
        `(stdout, stderr, exit_status, timed_out, cancelled)`.

        On timeout, or on `cancel_event` becoming set, `SIGINT` is sent to the shell's
        whole process group first, and the sentinel wait continues for
        `_TIMEOUT_GRACE_SECONDS` longer. A cancellation is reported as `cancelled=True`
        regardless of how cleanly it ends, since either way the user asked for it to stop.
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
        if self.networking is not None:
            self.networking.close()
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
        Safe to call more than once, or on an already-dead shell."""
        if not self.alive:
            return
        self._kill_process_group()


def _standing_interjection_provider(shell: PersistentShell) -> Callable[[], str | None]:
    """Build the `Session.register_standing_interjection` provider for a live persistent shell."""

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
    workspace root, and `SHELL`/`BASH` are always set to `bash_command`. Everything else
    in the klorb process's own environment is left out.

    ``NO_COLOR``, ``session_config.share_env`` passthrough, and ``session_config.set_env``
    overrides are written to the session env file and sourced inside the shell before the
    user command runs, rather than injected into this process-level dict. When a `bwrap`
    sandbox is in use this same dict is re-applied inside it via ``--clearenv`` + ``--setenv``;
    on the unsandboxed fallback path ``subprocess.Popen(..., env=...)`` receives exactly this
    dict, keeping the least-privilege intent intact regardless of which path runs.
    """
    env: dict[str, str] = {}
    for name in ("HOME", "USER"):
        if name in os.environ:
            env[name] = os.environ[name]
    env["WORKSPACE_ROOT"] = str(session_config.workspace.path.resolve())
    env["SHELL"] = bash_command
    env["BASH"] = bash_command
    return env


def _sandbox_notice() -> str:
    """One-time, human-readable explanation of why this command ran without an OS-level sandbox
    boundary. Only reached when `bwrap_available()` is `False`."""
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

    A signal death shows up one of two ways here:

    * Positive, `128 + signum` — the outer `bash` forked a real child for the target command
      and observed *that* child die by signal; bash itself then exits normally with the
      conventional `128 + signum` code, which is what `Popen.returncode` reports.
    * Negative, `-signum` — Python’s ordinary direct-child-signaled convention. This happens
      when bash’s own tail-call optimization `exec()`s directly into a simple trailing external
      command instead of forking. Both shapes are decoded the same way here so neither is
      mistaken for "exited normally with a large status code".
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
with no controlling tty. The process-group line's parenthesized number varies by
platform/session, so it's matched by prefix/suffix rather than a single exact string. Stripped
from the *front* of captured stderr only."""


def _strip_bash_shell_noise(text: str) -> str:
    """Remove `bash -i`'s fixed startup noise lines from the front of `text`, stopping at the
    first line that doesn't match either known noise pattern."""
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


_CMDS_WITH_NETWORK_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git": frozenset({"clone", "pull", "push", "fetch", "ls-remote"}),
    "go": frozenset({"get", "install"}),
    "cargo": frozenset({"add", "install", "build"}),
}
"""Which subcommand(s) of `git`/`go`/`cargo` actually reach the network — these three are used
constantly for purely local operations, so scanning every invocation's arguments for a domain
would be noisy and pointless."""

_BARE_HOST_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}(?::\d+)?$")
"""Matches a schemeless `host[:port]` token whose final label is alphabetic (a plausible TLD) —
deliberately conservative, so a bare package name/version specifier is never mistaken for a
domain."""


def _extract_literal_domain(token: str) -> str | None:
    """Return the domain `token` names, or `None` if it doesn't look like one at all -- tried as
    a full URL first (`scheme://host/...`), then, for a schemeless token, as a bare `host[:port]`
    wrapped in a synthetic `https://` scheme purely so `parse_domain`'s `urlparse`-based
    extraction has a netloc to find (the synthesized scheme is never itself meaningful). The bare
    form is gated on `_BARE_HOST_RE` first, so a non-URL-shaped argument (a package name, a flag
    value, a version specifier) is never wrapped and misread as a domain."""
    if "://" in token:
        try:
            return parse_domain(token)
        except ValueError:
            return None
    host_part = token.split("/", 1)[0]
    if not _BARE_HOST_RE.match(host_part):
        return None
    try:
        return parse_domain(f"https://{token}")
    except ValueError:
        return None


def _bash_network_scan_targets(argv: list[str]) -> list[str] | None:
    """The slice of `argv` (excluding `argv0`) that should actually be scanned for a literal
    domain, or `None` if this particular invocation of a recognized network client isn't
    network-shaped at all -- only commands in `_CMDS_WITH_NETWORK_SUBCOMMANDS` are
    subcommand-gated; every other recognized client has every non-flag argument scanned, since
    virtually any of them could be a URL for that kind of tool."""
    argv0 = argv[0]
    subcommands = _CMDS_WITH_NETWORK_SUBCOMMANDS.get(argv0)
    if subcommands is not None:
        if len(argv) >= 2 and argv[1] in subcommands:
            return argv[2:]
        # `go mod download` is a two-token subcommand, not a single entry `subcommands` can name.
        if argv0 == "go" and len(argv) >= 3 and argv[1] == "mod" and argv[2] == "download":
            return argv[3:]
        return None
    return argv[1:]


def _network_command_domains(argv: list[str], recognized_clients: frozenset[str]) -> list[str]:
    """Every distinct literal domain found in `argv`, in first-seen order, or `[]` if `argv0`
    isn't a recognized network client (`tools.bash.network.recognizedClients`) or none of its
    scanned arguments look like a domain at all. A `--flag=value` token is scanned both as a
    whole (in case the value itself is the whole token, e.g. a bare positional URL) and by its
    `value` half, so `--index-url=https://mirror.example.com/simple` is caught the same as a
    space-separated `--index-url https://mirror.example.com/simple` would be."""
    if not argv or argv[0] not in recognized_clients:
        return []
    targets = _bash_network_scan_targets(argv)
    if not targets:
        return []
    domains: list[str] = []
    for token in targets:
        if not token:
            continue
        candidates = [] if token.startswith("-") else [token]
        if "=" in token:
            candidates.append(token.split("=", 1)[1])
        for candidate in candidates:
            domain = _extract_literal_domain(candidate)
            if domain is not None and domain not in domains:
                domains.append(domain)
    return domains


class BashTool(InterruptibleTool):
    """Runs a single shell command string on the model's behalf.

    The command is parsed and every simple command extracted from the parse is evaluated
    against `context.session_config.command_rules`; every redirection's file target is
    evaluated against `readFiles`/`writeFiles` and `readDirs`/`writeDirs`. The overall verdict
    is the strictest of all contributors, enforced through `raise_if_not_allowed()`.

    Once permitted, the command runs via `bash -i -c`, wrapped in a `bwrap` sandbox whenever
    one can be created. Where it can't, the command falls back to unsandboxed execution with a
    one-time notice; the permission checks are enforced identically either way.

    `shell_lifetime` selects how long the underlying shell process lives: `"command"` spawns a
    fresh shell that exits when the command finishes; `"session"` reuses the one live persistent
    shell for this `Session`; `"new"` kills any existing persistent shell first, then creates a
    fresh one. At most one persistent shell exists per `Session` at a time.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._bash_command = context.process_config.bash_command
        self._timeout_seconds = context.process_config.bash_timeout_seconds
        self._spill_bytes = context.process_config.bash_spill_bytes
        self._shfmt_command = context.process_config.shfmt_command
        self._network_enabled = context.process_config.bash_network_enabled
        self._network_recognized_clients = frozenset(
            context.process_config.bash_network_recognized_clients)
        self._network_socks_port = context.process_config.bash_network_socks_port
        self._network_http_connect_port = context.process_config.bash_network_http_connect_port

    def name(self) -> str:
        return "Bash"

    def category(self) -> str:
        return "SHELL"

    def is_read_only(self) -> bool:
        return False

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
            "intent describes is scored as more risky. "
            "Many shell commands require explicit user permission and fail in autonomous mode. "
            "Built-in ListDir, Grep, FindFile, and ReadFile tools often work *better*."
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
        """Evaluate every simple command's verdict, every redirection target's directory-access
        verdict, and every reason the walker itself forced an "ask".

        A single `"deny"` anywhere short-circuits: the whole command is denied outright, with no
        `PermissionAskItem`s collected. Otherwise, every individual `"ask"` contributor becomes
        its own `PermissionAskItem` so `Session` can ask about each in series. `"allow"` (no
        deny, no ask items) means the whole command is permitted to run as-is.
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
                    f"run command: {' '.join(argv)}", resource=CommandResource(argv=tuple(argv)),
                    bash_context=BashCommandContext(
                        command_text=command, is_compound=is_compound,
                        item_command_text=simple_command.source_text, intent=intent)))

        if self._network_enabled:
            for simple_command in analysis.simple_commands:
                domains = _network_command_domains(
                    simple_command.argv, self._network_recognized_clients)
                for domain in domains:
                    if override is not None and domain in override.domains:
                        continue
                    domain_verdict = evaluate_domain(
                        self.context.session_config.bash_domain_rules, domain)
                    if domain_verdict == "deny":
                        denied = True
                    elif domain_verdict == "ask":
                        ask_items.append(PermissionAskItem(
                            f"Bash network access to {domain}",
                            resource=DomainResource(url=f"https://{domain}", rule_set="bash"),
                            bash_context=BashCommandContext(
                                command_text=command, is_compound=is_compound,
                                item_command_text=simple_command.source_text, intent=intent)))

        for forced_reason in analysis.forced_ask_reasons:
            if override is not None and forced_reason.reason in override.reasons:
                continue
            ask_items.append(PermissionAskItem(
                forced_reason.reason, resource=StructuralResource(reason=forced_reason.reason),
                bash_context=BashCommandContext(
                    command_text=command, is_compound=is_compound,
                    item_command_text=forced_reason.source_text, intent=intent)))

        for redirect in analysis.redirects:
            redirect_verdict, path, is_write = self._evaluate_redirect(redirect)
            if redirect_verdict == "deny":
                denied = True
            elif redirect_verdict == "ask":
                action = "write to" if is_write else "read"
                ask_items.append(PermissionAskItem(
                    f"{action} {path}", resource=PathResource(path=path, is_write=is_write),
                    bash_context=BashCommandContext(
                        command_text=command, is_compound=is_compound,
                        item_command_text=redirect.source_text, intent=intent)))

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
        on demand so a mid-session grant that widens `readDirs`/`writeDirs` is reflected the
        next time a command is sandboxed."""
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
        Only meaningful when `bwrap_available()`; callers gate on that themselves.

        `path_dirs` always includes the klorb process's own Python interpreter directory, on
        top of the ordinary `PATH`-derived top-up binds. Deliberately *not* resolved (symlinks
        followed): the relay stub is launched with the literal `sys.executable` string, so it's
        that literal path's own directory that must be bound, not its eventual symlink target's.
        """
        interpreter_dir = Path(sys.executable).parent
        return build_bwrap_argv(
            workspace_root=self.context.session_config.workspace.path.resolve(),
            home=home, env=env, dirs=self._compute_sandbox_dirs(home),
            path_dirs=[*path_dirs_from_env(), interpreter_dir])

    def _start_sandbox_networking(self) -> SandboxNetworking | None:
        """Stand up this invocation's network-egress proxy if `tools.bash.network.enabled`
        allows it, else `None`. Only ever called once `bwrap_available()` is already known
        `True`."""
        if not self._network_enabled:
            return None
        return start_sandbox_networking(
            self.context.session_config, sys.executable,
            self._network_socks_port, self._network_http_connect_port)

    def _env_file_source_command(self) -> str:
        """Return a shell snippet that sources this session's env file, or ``""`` when no
        session is available.  The env file is created (and populated with ``NO_COLOR``,
        ``share_env``, and ``set_env`` exports) on the first call for a given session.

        If the env file's directory is read-only, the export statements are inlined directly
        into the returned snippet instead of sourcing a file."""
        session = self.context.session
        if session is None:
            return ""
        try:
            env_file = session_env_file(session.id, self.context.session_config)
        except OSError:
            logger.debug("Env file directory read-only; inlining env exports")
            return "\n".join(_env_export_lines(self.context.session_config)) + "\n"
        return f'source "{env_file}"\n'

    def _execute(self, command: str) -> dict[str, Any]:
        workspace_root = self.context.session_config.workspace.path.resolve()
        env = build_bash_env(self.context.session_config, self._bash_command)
        home = env.get("HOME", str(Path.home()))
        rcfile = str(Path(home) / ".bashrc")
        sandboxed = bwrap_available()
        # Only meaningful when actually sandboxed: the unsandboxed fallback already has
        # unrestricted network access, so there is nothing for a proxy to gate there.
        networking = self._start_sandbox_networking() if sandboxed else None
        bootstrap_script = networking.bootstrap_script if networking is not None else ""
        env_file_source = self._env_file_source_command()
        full_command = f"unset PS1; unset PS2; {bootstrap_script}{env_file_source}{command}"
        inner_argv = [self._bash_command, "--rcfile", rcfile, "-i", "-c", full_command]
        # bwrap wraps the same inner invocation when a sandbox can actually be created here;
        # otherwise the command runs unsandboxed (with a one-time notice — see
        # _maybe_sandbox_notice), permission classification unaffected either way.
        if sandboxed:
            sandbox_env = dict(env)
            if networking is not None:
                sandbox_env.update(networking.env_vars)
            argv = self._bwrap_prefix(sandbox_env, Path(home)) + inner_argv
        else:
            argv = inner_argv

        try:
            return self._execute_inner(command, argv, env, home, workspace_root, networking)
        finally:
            if networking is not None:
                networking.close()

    def _execute_inner(
        self, command: str, argv: list[str], env: dict[str, str], home: str,
        workspace_root: Path, networking: SandboxNetworking | None,
    ) -> dict[str, Any]:
        """The body of `_execute()` proper, split out so `_execute()` itself can wrap it in a
        `try`/`finally` that unconditionally closes `networking` on every exit path."""
        tmp_dir = Path(tempfile.mkdtemp(prefix=_TMP_DIR_PREFIX))

        def cleanup_tmp_dir() -> None:
            # Whichever call site removes `tmp_dir` first -- the early return below on a missing
            # executable, or the end-of-command cleanup further down -- unregisters the atexit
            # backstop so it doesn't `shutil.rmtree` an already-gone directory again at process
            # exit.
            atexit.unregister(cleanup_tmp_dir)
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Registered immediately after creation, before anything else can raise, so this
        # directory is always swept on process exit even if klorb dies mid-command.
        atexit.register(cleanup_tmp_dir)
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
                    env=env, cwd=str(workspace_root), start_new_session=True,
                    pass_fds=() if networking is None else (networking.sandbox_fd,))
            except FileNotFoundError as exc:
                # stdout_f/stderr_f are still open here (this `with` block's __exit__ hasn't run
                # yet). Safe on POSIX regardless: unlink() only removes a directory entry, not the
                # underlying inode, so rmtree can remove both files and the now-empty directory
                # while fds to them are still live; the fds themselves stay valid until __exit__
                # closes them moments later, as this exception propagates out of the `with`.
                cleanup_tmp_dir()
                raise ValueError(f"{self._bash_command!r} not found; cannot run command") from exc

            # The sandboxed process now has its own fork-inherited copy of the control-channel
            # fd; this process's own copy is no longer needed, and must be released for
            # ProxyBackend's recv_fds() to ever see EOF once the sandboxed process actually dies
            # (a lingering open copy here would keep the control channel looking "live" forever).
            # No try/finally needed around this call: nothing between the Popen() above and here
            # can raise, and any other exception from Popen() itself is still caught by
            # `_execute()`'s own `try`/`finally`, whose `networking.close()` releases this fd too.
            if networking is not None:
                networking.release_sandbox_fd()

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
            cleanup_tmp_dir()
        else:
            self._grant_tmp_dir_read_access(tmp_dir)

        logger.info(
            "Bash command finished: exit_status=%s success=%s runtime=%.2fs",
            exit_status, success, runtime)

        result: dict[str, Any] = {
            "command": command,
            "exit_status": exit_status,
            "success": success,
            "failure_reason": failure_reason,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "stdout_file": stdout_file,
            "stderr_file": stderr_file,
            "runtime": runtime,
            "blocked_domains": networking.recorder.domains if networking is not None else [],
        }
        if sandbox_notice is not None:
            result["sandbox_notice"] = sandbox_notice
        return result

    def _maybe_sandbox_notice(self) -> str | None:
        """Return `_sandbox_notice()` the first time a command actually runs unsandboxed for the
        current `Session`, or every such call if there's no `Session` to dedupe against. Returns
        `None` whenever `bwrap_available()`."""
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
        # grant that widened it. Unsandboxed hosts get argv=[bash], snapshot=None, networking=None.
        if bwrap_available():
            networking = self._start_sandbox_networking()
            sandbox_env = dict(env)
            if networking is not None:
                sandbox_env.update(networking.env_vars)
            argv = self._bwrap_prefix(sandbox_env, Path(home)) + [self._bash_command]
            snapshot: frozenset[Path] | None = allowed_dir_snapshot(
                self._compute_sandbox_dirs(Path(home)))
        else:
            argv = [self._bash_command]
            snapshot = None
            networking = None
        try:
            shell = PersistentShell(
                argv, env, str(workspace_root), sandbox_snapshot=snapshot, networking=networking)
        except Exception:
            # PersistentShell's own subprocess.Popen() is what could raise here; nothing has
            # taken ownership of `networking` yet in that case, so this method must close it
            # itself rather than leaking it -- unlike the rest of this method's cleanup, which
            # `PersistentShell._mark_dead()` takes over once construction succeeds.
            if networking is not None:
                networking.close()
            raise
        if networking is not None:
            # The shell process now has its own fork-inherited copy of the control-channel fd --
            # see the matching comment in _execute_inner for why this process's own copy must be
            # released for ProxyBackend to ever see EOF once this persistent shell dies.
            networking.release_sandbox_fd()
            shell.run_command(networking.bootstrap_script, self._timeout_seconds)
        # PS1='x' satisfies the `[ -z "$PS1" ] && return` guard most real .bashrc files start
        # with (see PersistentShell's docstring and docs/adrs/bash-env-uses-clearenv-plus-
        # shareenv-setenv-plus-forced-rcfile.md) without ever printing a prompt -- this shell was
        # deliberately launched without -i, so nothing reads $PS1 as an actual prompt string.
        # Discarding the result: this is harness bootstrapping, not a model-visible command: a
        # missing/erroring rcfile shouldn't fail shell creation, just leave PATH/toolchain setup
        # up to whatever build_bash_env already put in the environment.
        env_file_source = self._env_file_source_command()
        bootstrap = f'{env_file_source}PS1=x\n[ -f "{rcfile}" ] && source "{rcfile}"\nunset PS1 PS2\n'
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
        `SIGKILL` the old sandbox."""
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
        if shell.networking is not None:
            # This shell's ProxyBackend/recorder outlive any one command, so the recorder is
            # reset immediately before each command runs -- otherwise this response could report
            # a prior command's blocked domains instead of (or alongside) its own.
            shell.networking.recorder.reset()

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
            "blocked_domains": shell.networking.recorder.domains if shell.networking else [],
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

        * The allowed-dir set is unchanged (the overwhelmingly common case): the same shell,
          `False`, `None`.
        * The allowed-dir set grew and the shell has no live background jobs: a freshly rebuilt
          shell, `True`, `None`.
        * It grew but the shell has live background jobs: the original shell (untouched), `False`,
          and a non-`None` refusal string.
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
        """The response for a reconcile-on-grow refusal: the command did not run, the existing
        terminal is left alive and untouched, and `failure_reason` explains how to pick up the
        new grant."""
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
            "blocked_domains": [],
        }

    def _finalize_persistent_output(
        self, stdout: str, stderr: str,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        """Spill `stdout`/`stderr` to a fresh per-call directory if either exceeds
        `bash_spill_bytes`."""
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
        """Auto-grant read access to `tmp_dir` so a follow-up `ReadFile`/`Grep` call against
        a spilled `stdout_file`/`stderr_file` doesn't itself hit an "ask"."""
        read_dirs = self.context.session_config.read_dirs
        self.context.session_config.read_dirs = DirRules(
            deny=list(read_dirs.deny), ask=list(read_dirs.ask), allow=[*read_dirs.allow, tmp_dir])

    def is_success(self, args: dict[str, Any], result: Any, error: str | None) -> bool:
        """A shell command that ran but failed doesn't raise — `apply()` returns a result
        dict carrying `"success": False`. The base class's `error is None` heuristic isn't
        enough here: honor the result's own `"success"` flag once no exception was raised.
        """
        if error is not None:
            return False
        return bool(result.get("success")) if isinstance(result, dict) else True

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Prefix the rendered command with the model's own `intent` (when given), so the
        history scroll shows what the command is *for* alongside what it actually runs."""
        command = args.get("command", "?")
        intent = args.get("intent")
        label = f"{intent}\n$ {command}" if intent else command
        if error is not None:
            return f"Bash: {label} failed: {error}"
        if not isinstance(result, dict):
            return f"Bash: {label}"
        status = "ok" if result.get("success") else f"exit {result.get('exit_status')}"
        return f"Bash: {label}\n({status}, {result.get('runtime', 0):.2f}s)"

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
