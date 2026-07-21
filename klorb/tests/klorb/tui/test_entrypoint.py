# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.entrypoint: run_repl's crash handling."""

import contextlib
import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tui.conftest import _isolated_data_dir, _repl_app_for_workspace, _sample_message, _session

from klorb.logging_config import CrashLogTee
from klorb.tui.app import ReplApp
from klorb.tui.entrypoint import _handle_repl_crash
from klorb.workspace import TrustManager
from klorb.workspace.last_session import last_session_path, read_last_session


async def _crash_running_app(app: ReplApp) -> None:
    """Run `app` under Textual's test harness and force it to crash mid-run by feeding
    `App._handle_exception` a real, currently-handled exception (`rich.traceback.Traceback`
    requires an active `except` block to render from). `run_test()` re-raises the crash's
    exception once the app finishes shutting down, mirroring how a real unhandled exception
    would surface to a test framework.
    """
    async with app.run_test():
        try:
            raise RuntimeError("boom")
        except RuntimeError as error:
            app._handle_exception(error)


async def test_error_console_crash_dump_is_captured_by_crash_log_tee(tmp_path: Path) -> None:
    """`ReplApp.error_console.file` swapped for a `CrashLogTee` (as `run_repl` does) captures
    the same traceback Textual would otherwise print only to stderr, exercising the real
    `App._handle_exception` -> `App._print_error_renderables` crash path rather than calling
    `CrashLogTee` in isolation.
    """
    session = _session(MagicMock())
    app = ReplApp(session=session)
    stream = io.StringIO()
    log_path = tmp_path / "crash.log"
    tee = CrashLogTee(stream, log_path)
    app.error_console.file = tee

    with pytest.raises(RuntimeError, match="boom"):
        await _crash_running_app(app)

    assert app.return_code == 1
    assert "boom" in stream.getvalue()
    assert tee.opened_log_path() == log_path
    assert "boom" in log_path.read_text(encoding="utf-8")


async def test_handle_repl_crash_saves_session_for_trusted_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager, model="crash/model")
    crash_tee = CrashLogTee(io.StringIO(), tmp_path / "crash.log")

    async with app.run_test():
        app._session.load_messages([_sample_message("hi")])
        _handle_repl_crash(app, crash_tee)

    state = read_last_session(workspace)
    assert state is not None
    assert state.config.model == "crash/model"
    assert [m.content for m in state.messages] == ["hi"]


async def test_handle_repl_crash_skips_save_for_untrusted_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager)
    crash_tee = CrashLogTee(io.StringIO(), tmp_path / "crash.log")

    async with app.run_test():
        _handle_repl_crash(app, crash_tee)

    assert read_last_session(workspace) is None


async def test_handle_repl_crash_prints_pointer_messages_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager)
    log_path = tmp_path / "crash.log"
    crash_tee = CrashLogTee(io.StringIO(), log_path)
    crash_tee.write("boom")  # opens the file so opened_log_path() reports it below.

    stderr = io.StringIO()
    async with app.run_test():
        with contextlib.redirect_stderr(stderr):
            _handle_repl_crash(app, crash_tee)

    output = stderr.getvalue()
    assert str(log_path) in output
    assert str(last_session_path(workspace)) in output
