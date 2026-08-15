# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli.system_prompt."""

from collections.abc import Iterator
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from klorb.cli.system_prompt import run_system_prompt_cli
from klorb.process_config import ProcessConfig
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def stub_process_config() -> Iterator[MagicMock]:
    """Replace file-backed process config loading with a fresh default for every test in
    this module, so tests don't depend on `/etc`, `$HOME`, or the repo's own `cwd` being
    free of a stray `klorb-config.json`. Tests that care about the loading behavior itself
    patch or call `klorb.process_config.load_process_config` directly instead.
    """
    with patch("klorb.cli.system_prompt.load_process_config", return_value=ProcessConfig()) as mock_load:
        yield mock_load


def test_run_system_prompt_cli_defaults_role_to_operator(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.system_prompt.load_dotenv"):
        with patch("klorb.cli.system_prompt.TrustManager") as mock_tm_cls:
            mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
                path=Path.cwd(), trusted=False)
            exit_code = run_system_prompt_cli([])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "## System Prompt (default_sys.md)" in out
    assert "## Role-Specific Prompt (role: operator)" in out
    assert "## Tool Definitions" in out
    assert "## Token Count Summary" in out
    assert "default_sys.md:" in out
    assert "role-specific prompt:" in out
    assert "tool definitions:" in out
    assert "total" in out


def test_run_system_prompt_cli_passes_explicit_role(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.system_prompt.load_dotenv"):
        with patch("klorb.cli.system_prompt.TrustManager") as mock_tm_cls:
            mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
                path=Path.cwd(), trusted=False)
            exit_code = run_system_prompt_cli(["--role", "auditor"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "## Role-Specific Prompt (role: auditor)" in out


def test_run_system_prompt_cli_passes_explicit_model(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.system_prompt.load_dotenv"):
        with patch("klorb.cli.system_prompt.TrustManager") as mock_tm_cls:
            mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
                path=Path.cwd(), trusted=False)
            exit_code = run_system_prompt_cli(["--model", "some/model"])

    assert exit_code == 0


def test_run_system_prompt_cli_passes_config_flag_path(
    stub_process_config: MagicMock,
) -> None:
    with patch("klorb.cli.system_prompt.load_dotenv"):
        with patch("klorb.cli.system_prompt.TrustManager") as mock_tm_cls:
            mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
                path=Path.cwd(), trusted=False)
            run_system_prompt_cli(["--config", "/some/extra-config.json"])

    stub_process_config.assert_called_once_with(
        config_flag_path=Path("/some/extra-config.json"), cwd=mock.ANY, workspace=mock.ANY)
