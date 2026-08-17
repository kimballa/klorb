# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.entrypoint: run_repl's crash handling and its quit-on-success final-
response print."""

import contextlib
import io
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from tui.conftest import _isolated_data_dir, _repl_app_for_workspace, _sample_message, _session

from klorb.logging_config import CrashLogTee
from klorb.session import SessionConfig
from klorb.tui.app import ReplApp
from klorb.tui.entrypoint import _handle_repl_crash, run_repl
from klorb.workspace import TrustManager
from klorb.workspace.session_store import read_session_state, read_sessions_index


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


async def test_error_console_crash_dump_is_captured_by_crash_log_tee(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """`ReplApp.error_console.file` swapped for a `CrashLogTee` (as `run_repl` does) captures
    the same traceback Textual would otherwise print only to stderr, exercising the real
    `App._handle_exception` -> `App._print_error_renderables` crash path rather than calling
    `CrashLogTee` in isolation.
    """
    session = _session(MagicMock(), make_session_config)
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
    make_session_config: Callable[..., SessionConfig],
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, make_session_config, trust_manager, model="crash/model")
    crash_tee = CrashLogTee(io.StringIO(), tmp_path / "crash.log")

    async with app.run_test():
        app._session.load_messages([_sample_message("hi")])
        _handle_repl_crash(app, crash_tee)

    index = read_sessions_index(workspace)
    assert len(index.recent_sessions) == 1
    state = read_session_state(workspace, index.recent_sessions[0].subdir)
    assert state is not None
    assert state.config.model == "crash/model"
    assert [m.content for m in state.messages] == ["hi"]


async def test_handle_repl_crash_skips_save_for_untrusted_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    make_session_config: Callable[..., SessionConfig],
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=False)
    app = _repl_app_for_workspace(workspace, make_session_config, trust_manager)
    crash_tee = CrashLogTee(io.StringIO(), tmp_path / "crash.log")

    async with app.run_test():
        _handle_repl_crash(app, crash_tee)

    assert read_sessions_index(workspace).recent_sessions == []


async def test_handle_repl_crash_prints_pointer_messages_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    make_session_config: Callable[..., SessionConfig],
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, make_session_config, trust_manager)
    log_path = tmp_path / "crash.log"
    crash_tee = CrashLogTee(io.StringIO(), log_path)
    crash_tee.write("boom")  # opens the file so opened_log_path() reports it below.

    stderr = io.StringIO()
    async with app.run_test():
        with contextlib.redirect_stderr(stderr):
            _handle_repl_crash(app, crash_tee)

    output = stderr.getvalue()
    assert str(log_path) in output
    assert "session state saved" in output


def _mock_repl_app(*, final_turn_response: str | None, return_code: int = 0) -> MagicMock:
    """A stand-in for `run_repl()`'s `ReplApp(...)` construction: a `run()` that does nothing
    (no real Textual event loop) plus the two attributes `run_repl()` reads afterward."""
    mock_app = MagicMock()
    mock_app.return_code = return_code
    mock_app._final_turn_response = final_turn_response
    return mock_app


def test_run_repl_prints_final_turn_response_after_teardown(
    capsys: pytest.CaptureFixture[str],
    make_session_config: Callable[..., SessionConfig],
) -> None:
    """A `--quit-on-success` exit leaves `ReplApp._final_turn_response` set to the triggering
    turn's response text; `run_repl()` must print it to stdout via a plain `print()` (not the
    logger) once `App.run()` has returned and the TUI has torn down, so the agent's final answer
    is still visible on the terminal -- see docs/specs/terminal-repl.md."""
    mock_app = _mock_repl_app(final_turn_response="the final answer")
    with patch("klorb.tui.entrypoint.ReplApp", return_value=mock_app):
        run_repl(session=_session(MagicMock(), make_session_config))

    assert capsys.readouterr().out == "the final answer\n"


def test_run_repl_prints_nothing_when_quit_on_success_never_fired(
    capsys: pytest.CaptureFixture[str],
    make_session_config: Callable[..., SessionConfig],
) -> None:
    """`_final_turn_response` stays `None` for every exit path other than a `--quit-on-success`
    close (Ctrl+Q, a crash, an ordinary interrupt) -- `run_repl()` must not print anything for
    those."""
    mock_app = _mock_repl_app(final_turn_response=None)
    with patch("klorb.tui.entrypoint.ReplApp", return_value=mock_app):
        run_repl(session=_session(MagicMock(), make_session_config))

    assert capsys.readouterr().out == ""
