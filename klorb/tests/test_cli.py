# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli."""

from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from klorb import cli


def test_main_prints_prompt_response(capsys: pytest.CaptureFixture[str]) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "model reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "-m", "what is 2+2?"]):
            cli.main()

    mock_session.run_one_shot.assert_called_once_with("what is 2+2?")
    assert capsys.readouterr().out == "model reply\n"


def test_main_accepts_long_message_flag() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "model reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "--message", "what is 2+2?"]):
            cli.main()

    mock_session.run_one_shot.assert_called_once_with("what is 2+2?")


def test_main_passes_custom_model() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "--model", "some/model", "-m", "hi"]):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.model == "some/model"
    mock_session.run_one_shot.assert_called_once_with("hi")


def test_main_one_shot_config_is_not_interactive() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.interactive is False


def test_main_starts_repl_when_no_prompt_given() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb"]):
                cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.interactive is True
    mock_run_repl.assert_called_once_with(mock_session, initial_message=None)


def test_main_message_with_interactive_flag_starts_repl_with_initial_message() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb", "--interactive", "-m", "hi"]):
                cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.interactive is True
    mock_run_repl.assert_called_once_with(mock_session, initial_message="hi")


def test_main_no_message_and_explicit_no_interactive_errors() -> None:
    with patch("sys.argv", ["klorb", "--no-interactive"]):
        with pytest.raises(SystemExit):
            cli.main()


def test_main_one_shot_skips_session_log_by_default() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.configure_logging") as mock_configure_logging:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                cli.main()

    mock_configure_logging.assert_called_once_with(repl_mode=False, session_log=False)


def test_main_one_shot_writes_session_log_when_requested() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.configure_logging") as mock_configure_logging:
            with patch("sys.argv", ["klorb", "--session-log", "-m", "hi"]):
                cli.main()

    mock_configure_logging.assert_called_once_with(repl_mode=False, session_log=True)


def test_main_repl_writes_session_log_by_default() -> None:
    with patch("klorb.cli.run_repl"):
        with patch("klorb.cli.configure_logging") as mock_configure_logging:
            with patch("sys.argv", ["klorb"]):
                cli.main()

    mock_configure_logging.assert_called_once_with(repl_mode=True, session_log=True)


def test_main_repl_skips_session_log_when_disabled() -> None:
    with patch("klorb.cli.run_repl"):
        with patch("klorb.cli.configure_logging") as mock_configure_logging:
            with patch("sys.argv", ["klorb", "--no-session-log"]):
                cli.main()

    mock_configure_logging.assert_called_once_with(repl_mode=True, session_log=False)


def test_main_passes_session_log_path_into_config() -> None:
    mock_session = MagicMock()
    log_path = Path("/tmp/some-session.log")
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.configure_logging", return_value=log_path):
            with patch("klorb.cli.run_repl"):
                with patch("sys.argv", ["klorb"]):
                    cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.log_filename == str(log_path)


def test_main_passes_no_log_filename_when_session_log_disabled() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.configure_logging", return_value=None):
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.log_filename is None
