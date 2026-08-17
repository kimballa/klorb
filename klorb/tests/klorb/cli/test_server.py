# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli.server."""

from collections.abc import Iterator
from pathlib import Path
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from klorb.cli.server import run_server_cli
from klorb.logging_config import session_log_path
from klorb.process_config import ProcessConfig
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def stub_process_config() -> Iterator[MagicMock]:
    """Replace file-backed process config loading with a fresh default for every test in
    this module, so tests don't depend on `/etc`, `$HOME`, or the repo's own `cwd` being
    free of a stray `klorb-config.json`. Tests that care about the loading behavior itself
    patch or call `klorb.process_config.load_process_config` directly instead.
    """
    with patch("klorb.cli.server.load_process_config", return_value=ProcessConfig()) as mock_load:
        yield mock_load


def test_run_server_cli_runs_acp_server_against_stdio() -> None:
    mock_streams = MagicMock()
    mock_server = MagicMock()
    mock_server.run = AsyncMock(return_value=0)
    with patch("klorb.cli.server.ServerStreams") as mock_streams_cls:
        mock_streams_cls.from_stdio = AsyncMock(return_value=mock_streams)
        with patch("klorb.cli.server.AcpServer", return_value=mock_server) as mock_server_cls:
            exit_code = run_server_cli([])

    mock_streams_cls.from_stdio.assert_called_once_with()
    mock_server_cls.assert_called_once_with(mock_streams, mock.ANY, config_flag_path=None)
    mock_server.run.assert_called_once_with()
    assert exit_code == 0


def test_run_server_cli_returns_zero_on_keyboard_interrupt() -> None:
    with patch("klorb.cli.server.ServerStreams") as mock_streams_cls:
        mock_streams_cls.from_stdio = AsyncMock(side_effect=KeyboardInterrupt())
        exit_code = run_server_cli([])

    assert exit_code == 0


def test_run_server_cli_passes_config_flag_path(
    stub_process_config: MagicMock,
) -> None:
    with patch("klorb.cli.server.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        with patch("klorb.cli.server.ServerStreams") as mock_streams_cls:
            mock_streams_cls.from_stdio = AsyncMock(return_value=MagicMock())
            with patch(
                "klorb.cli.server.AcpServer", return_value=MagicMock(run=AsyncMock(return_value=0)),
            ) as mock_server_cls:
                run_server_cli(["--config", "/some/extra-config.json"])

    stub_process_config.assert_called_once_with(
        config_flag_path=Path("/some/extra-config.json"), cwd=mock.ANY, workspace=mock.ANY)
    mock_server_cls.assert_called_once_with(
        mock.ANY, mock.ANY, config_flag_path=Path("/some/extra-config.json"))


def test_run_server_cli_passes_no_config_flag_path_by_default(
    stub_process_config: MagicMock,
) -> None:
    with patch("klorb.cli.server.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        with patch("klorb.cli.server.ServerStreams") as mock_streams_cls:
            mock_streams_cls.from_stdio = AsyncMock(return_value=MagicMock())
            with patch("klorb.cli.server.AcpServer", return_value=MagicMock(run=AsyncMock(return_value=0))):
                run_server_cli([])

    stub_process_config.assert_called_once_with(
        config_flag_path=None, cwd=mock.ANY, workspace=mock.ANY)


def test_run_server_cli_logs_config_warnings(
    stub_process_config: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    stub_process_config.return_value = ProcessConfig(config_warnings=["bad klorb-config.json"])
    with patch("klorb.cli.server.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
            path=Path.cwd(), trusted=False)
        with patch("klorb.cli.server.configure_logging"):
            with patch("klorb.cli.server.ServerStreams") as mock_streams_cls:
                mock_streams_cls.from_stdio = AsyncMock(return_value=MagicMock())
                with patch(
                    "klorb.cli.server.AcpServer", return_value=MagicMock(run=AsyncMock(return_value=0))
                ):
                    with caplog.at_level("WARNING"):
                        run_server_cli([])

    assert "bad klorb-config.json" in caplog.text


def test_run_server_cli_configures_logging_to_stderr_and_session_log_file() -> None:
    """`repl_mode=False` sends records to a plain `StreamHandler` (stderr) in addition to the
    session log file, so a client that captures the server subprocess's stderr (e.g. the
    VSCode plugin) sees debug-level output too -- see docs/specs/paths-and-logging.md.
    `stderr_json=True` formats each stderr line as one JSON object so that client can parse
    each record instead of treating it as opaque text."""
    with patch("klorb.cli.server.generate_session_id", return_value="some-session-id"):
        with patch("klorb.cli.server.configure_logging") as mock_configure_logging:
            with patch("klorb.cli.server.ServerStreams") as mock_streams_cls:
                mock_streams_cls.from_stdio = AsyncMock(return_value=MagicMock())
                with patch(
                    "klorb.cli.server.AcpServer", return_value=MagicMock(run=AsyncMock(return_value=0))
                ):
                    run_server_cli([])

    mock_configure_logging.assert_called_once_with(
        repl_mode=False, log_path=session_log_path("server-some-session-id"),
        stderr_json=True)
