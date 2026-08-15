# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli.init."""

from unittest.mock import patch

import pytest

from klorb.cli.init import run_init_cli
from klorb.klorb_init import InitError


def test_run_init_cli_defaults_scope_and_prints_messages_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.init.default_scope", return_value="user"):
        with patch("klorb.cli.init.run_init", return_value=["msg one", "msg two"]) as mock_run_init:
            exit_code = run_init_cli([])

    assert exit_code == 0
    mock_run_init.assert_called_once_with("user", force=False)
    assert capsys.readouterr().err == "msg one\nmsg two\n"


def test_run_init_cli_passes_explicit_scope_and_force() -> None:
    with patch("klorb.cli.init.run_init", return_value=[]) as mock_run_init:
        run_init_cli(["--system", "--force"])

    mock_run_init.assert_called_once_with("system", force=True)


def test_run_init_cli_rejects_conflicting_scope_flags() -> None:
    with pytest.raises(SystemExit):
        run_init_cli(["--system", "--user"])


def test_run_init_cli_returns_one_and_prints_error_on_init_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.init.run_init", side_effect=InitError("boom")):
        exit_code = run_init_cli(["--user"])

    assert exit_code == 1
    assert "boom" in capsys.readouterr().err
