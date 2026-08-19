# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli.show_config."""

import json
from collections.abc import Iterator
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from klorb.cli.show_config import run_show_config_cli
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def stub_process_config() -> Iterator[MagicMock]:
    """Replace file-backed process config loading with a fresh default for every test in
    this module, so tests don't depend on `/etc`, `$HOME`, or the repo's own `cwd` being
    free of a stray `klorb-config.json`.
    """
    with patch("klorb.cli.show_config.load_process_config", return_value=ProcessConfig()) as mock_load:
        yield mock_load


def test_run_show_config_cli_returns_valid_json(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.show_config.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        exit_code = run_show_config_cli([])

    assert exit_code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert isinstance(payload, dict)
    assert "sessionDefaults" in payload


def test_run_show_config_cli_uses_dotted_camel_case_keys(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.show_config.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        run_show_config_cli([])

    payload = json.loads(capsys.readouterr().out)
    # Top-level keys should use dotted camelCase, not Python attribute names.
    assert "tools.bash.command" in payload
    assert "bash_command" not in payload
    # Session defaults should use dotted camelCase.
    session = payload["sessionDefaults"]
    assert "model" in session
    assert "tools.maxCallsPerTurn" in session
    assert "max_tool_calls_per_turn" not in session


def test_run_show_config_cli_includes_permission_rules(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.show_config.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        run_show_config_cli([])

    payload = json.loads(capsys.readouterr().out)
    session = payload["sessionDefaults"]
    assert "readDirs" in session
    assert "writeDirs" in session
    assert "commandRules" in session
    assert "skillRules" in session
    # Each should have deny/ask/allow structure.
    for key in ("readDirs", "writeDirs", "commandRules", "skillRules"):
        assert set(session[key].keys()) == {"deny", "ask", "allow"}


def test_run_show_config_cli_does_not_include_runtime_only_keys(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.show_config.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        run_show_config_cli([])

    payload = json.loads(capsys.readouterr().out)
    # These are not config-file-readable keys.
    assert "config_warnings" not in payload
    assert "argv" not in payload
    assert "session_cli_flags" not in payload
    session = payload["sessionDefaults"]
    assert "approved_scopes" not in session
    assert "permission_framework" not in session
    assert "workspace" not in session


def test_run_show_config_cli_passes_config_flag_path(
    stub_process_config: MagicMock,
) -> None:
    with patch("klorb.cli.show_config.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        run_show_config_cli(["--config", "/some/extra-config.json"])

    stub_process_config.assert_called_once_with(
        config_flag_path=Path("/some/extra-config.json"), cwd=mock.ANY, workspace=mock.ANY)


def test_run_show_config_cli_passes_no_config_flag_path_by_default(
    stub_process_config: MagicMock,
) -> None:
    with patch("klorb.cli.show_config.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        run_show_config_cli([])

    stub_process_config.assert_called_once_with(
        config_flag_path=None, cwd=mock.ANY, workspace=mock.ANY)


def test_run_show_config_cli_includes_session_config(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    process_config = ProcessConfig(
        session=SessionConfig(model="some/model", max_tool_calls_per_turn=10))
    stub_process_config.return_value = process_config
    with patch("klorb.cli.show_config.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        run_show_config_cli([])

    payload = json.loads(capsys.readouterr().out)
    session = payload["sessionDefaults"]
    assert session["model"] == "some/model"
    assert session["tools.maxCallsPerTurn"] == 10
