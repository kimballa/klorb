# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli."""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from klorb import cli


def test_main_prints_prompt_response(capsys: pytest.CaptureFixture[str]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "model reply"
    with patch("klorb.cli.OpenRouterApiProvider", return_value=mock_provider):
        with patch("sys.argv", ["klorb", "-m", "what is 2+2?"]):
            cli.main()

    mock_provider.send_prompt.assert_called_once_with("what is 2+2?", model=cli.DEFAULT_MODEL)
    assert capsys.readouterr().out == "model reply\n"


def test_main_accepts_long_message_flag() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "model reply"
    with patch("klorb.cli.OpenRouterApiProvider", return_value=mock_provider):
        with patch("sys.argv", ["klorb", "--message", "what is 2+2?"]):
            cli.main()

    mock_provider.send_prompt.assert_called_once_with("what is 2+2?", model=cli.DEFAULT_MODEL)


def test_main_passes_custom_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "reply"
    with patch("klorb.cli.OpenRouterApiProvider", return_value=mock_provider):
        with patch("sys.argv", ["klorb", "--model", "some/model", "-m", "hi"]):
            cli.main()

    mock_provider.send_prompt.assert_called_once_with("hi", model="some/model")


def test_main_starts_repl_when_no_prompt_given() -> None:
    with patch("klorb.cli.run_repl") as mock_run_repl:
        with patch("sys.argv", ["klorb"]):
            cli.main()

    mock_run_repl.assert_called_once_with(model=cli.DEFAULT_MODEL)


def test_main_one_shot_skips_session_log_by_default() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "reply"
    with patch("klorb.cli.OpenRouterApiProvider", return_value=mock_provider):
        with patch("klorb.cli.configure_logging") as mock_configure_logging:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                cli.main()

    mock_configure_logging.assert_called_once_with(repl_mode=False, session_log=False)


def test_main_one_shot_writes_session_log_when_requested() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "reply"
    with patch("klorb.cli.OpenRouterApiProvider", return_value=mock_provider):
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
