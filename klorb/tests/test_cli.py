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
        with patch("sys.argv", ["klorb", "what is 2+2?"]):
            cli.main()

    mock_provider.send_prompt.assert_called_once_with("what is 2+2?", model=cli.DEFAULT_MODEL)
    assert capsys.readouterr().out == "model reply\n"


def test_main_passes_custom_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "reply"
    with patch("klorb.cli.OpenRouterApiProvider", return_value=mock_provider):
        with patch("sys.argv", ["klorb", "--model", "some/model", "hi"]):
            cli.main()

    mock_provider.send_prompt.assert_called_once_with("hi", model="some/model")
