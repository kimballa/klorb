# © Copyright 2026 Aaron Kimball
"""Shell integration for klorb's TUI: running a single `!`-prefixed command a user typed
directly into the REPL, via a configurable shell binary (`ProcessConfig.shell_command`).
"""

import subprocess
import threading
import time
from typing import IO, Callable, Tuple

_POLL_INTERVAL_SECONDS = 0.1
"""How often `UserShellCommand.run()` wakes up to check `cancel_event`/its timeout deadline
while the process is still running. Small enough that a Ctrl+C interrupt or a just-elapsed
timeout is noticed promptly, large enough not to busy-loop."""


class ShellCommandTimedOut(Exception):
    """Raised by `UserShellCommand.run()` when the process is still running once `timeout`
    seconds have elapsed; the process has already been killed by the time this is raised."""


class ShellCommandCancelled(Exception):
    """Raised by `UserShellCommand.run()` when `cancel_event` becomes set (e.g. the REPL's
    Ctrl+C interrupt handler) while the process is still running; the process has already
    been killed by the time this is raised."""


class UserShellCommand:
    """Run a single shell command directly entered by the user, via `shell_path -c command`.

    This class trusts the command directly because it came from the user. It must not be
    used to execute a command provided by the model.
    """

    def __init__(self, command: str, shell_path: str) -> None:
        self._command = command
        self._shell_path = shell_path

    def run(
        self,
        *,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Tuple[str, str, int]:
        """Run the command to completion and return `(stdout, stderr, returncode)`.

        If given, `on_stdout`/`on_stderr` are called once per line of output, as soon as it's
        available, rather than only after the process exits — each stream is pumped on its
        own background thread so a command that writes slowly to one but not the other (or
        writes lines out of order between the two) doesn't stall either callback.

        Raises `ShellCommandTimedOut` if `timeout` elapses before the process exits on its
        own, or `ShellCommandCancelled` if `cancel_event` becomes set first — in both cases
        the process is killed before the exception is raised.
        """
        try:
            process = subprocess.Popen(
                [self._shell_path, "--login", "-c", self._command],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except FileNotFoundError:
            message = f"{self._shell_path} not found; cannot execute command"
            if on_stderr is not None:
                on_stderr(message)
            return ("", message, 127)

        assert process.stdout is not None
        assert process.stderr is not None

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def pump(stream: IO[str], sink: list[str], callback: Callable[[str], None] | None) -> None:
            for line in iter(stream.readline, ""):
                sink.append(line)
                if callback is not None:
                    callback(line)
            stream.close()

        stdout_thread = threading.Thread(
            target=pump, args=(process.stdout, stdout_lines, on_stdout), daemon=True)
        stderr_thread = threading.Thread(
            target=pump, args=(process.stderr, stderr_lines, on_stderr), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        deadline = None if timeout is None else time.monotonic() + timeout
        interrupted: type[Exception] | None = None
        while True:
            try:
                process.wait(timeout=_POLL_INTERVAL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                if cancel_event is not None and cancel_event.is_set():
                    interrupted = ShellCommandCancelled
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    interrupted = ShellCommandTimedOut
                    break

        if interrupted is not None:
            process.kill()
            process.wait()
        stdout_thread.join()
        stderr_thread.join()

        if interrupted is not None:
            raise interrupted()

        return ("".join(stdout_lines), "".join(stderr_lines), process.returncode)
