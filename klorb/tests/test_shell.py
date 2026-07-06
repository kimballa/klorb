# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.shell."""

import threading
import time

import pytest

from klorb.tui.shell import ShellCommandCancelled, ShellCommandTimedOut, UserShellCommand

SHELL_PATH = "/bin/bash"


def test_run_returns_stdout_stderr_and_returncode() -> None:
    command = UserShellCommand("echo hello; echo world 1>&2; exit 3", shell_path=SHELL_PATH)

    stdout, stderr, rc = command.run()

    assert stdout == "hello\n"
    assert stderr == "world\n"
    assert rc == 3


def test_run_reports_command_not_found_as_nonzero_exit() -> None:
    command = UserShellCommand("definitely-not-a-real-command-xyz", shell_path=SHELL_PATH)

    stdout, stderr, rc = command.run()

    assert stdout == ""
    assert rc != 0


def test_run_with_missing_shell_binary_returns_a_clear_error() -> None:
    command = UserShellCommand("echo hi", shell_path="/no/such/shell")

    stdout, stderr, rc = command.run()

    assert stdout == ""
    assert "/no/such/shell not found" in stderr
    assert rc == 127


def test_run_streams_stdout_lines_as_they_arrive() -> None:
    command = UserShellCommand("echo one; echo two; echo three", shell_path=SHELL_PATH)
    seen: list[str] = []

    command.run(on_stdout=seen.append)

    assert seen == ["one\n", "two\n", "three\n"]


def test_run_streams_stderr_lines_separately_from_stdout() -> None:
    command = UserShellCommand("echo out; echo err 1>&2", shell_path=SHELL_PATH)
    stdout_seen: list[str] = []
    stderr_seen: list[str] = []

    command.run(on_stdout=stdout_seen.append, on_stderr=stderr_seen.append)

    assert stdout_seen == ["out\n"]
    assert stderr_seen == ["err\n"]


def test_run_raises_timeout_and_kills_the_process() -> None:
    command = UserShellCommand("sleep 5", shell_path=SHELL_PATH)

    start = time.monotonic()
    with pytest.raises(ShellCommandTimedOut):
        command.run(timeout=0.3)
    elapsed = time.monotonic() - start

    assert elapsed < 4.0


def test_run_raises_cancelled_when_cancel_event_is_set() -> None:
    command = UserShellCommand("sleep 5", shell_path=SHELL_PATH)
    cancel_event = threading.Event()

    def cancel_shortly() -> None:
        time.sleep(0.2)
        cancel_event.set()

    canceller = threading.Thread(target=cancel_shortly)
    canceller.start()
    start = time.monotonic()
    with pytest.raises(ShellCommandCancelled):
        command.run(cancel_event=cancel_event)
    elapsed = time.monotonic() - start
    canceller.join()

    assert elapsed < 4.0
