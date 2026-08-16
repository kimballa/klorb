# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli.index."""

from unittest.mock import MagicMock

import pytest

from klorb.cli import index as index_module
from klorb.cli.index import run_index_cli


def test_run_index_cli_dispatches_search(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_search = MagicMock(return_value=0)
    monkeypatch.setitem(index_module._ACTIONS, "search", mock_search)

    exit_code = run_index_cli(["search", "some query", "--json"])

    assert exit_code == 0
    mock_search.assert_called_once_with(["some query", "--json"])


def test_run_index_cli_dispatches_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_scan = MagicMock(return_value=0)
    monkeypatch.setitem(index_module._ACTIONS, "scan", mock_scan)

    exit_code = run_index_cli(["scan", "--rebuild"])

    assert exit_code == 0
    mock_scan.assert_called_once_with(["--rebuild"])


def test_run_index_cli_dispatches_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_stats = MagicMock(return_value=0)
    monkeypatch.setitem(index_module._ACTIONS, "stats", mock_stats)

    exit_code = run_index_cli(["stats"])

    assert exit_code == 0
    mock_stats.assert_called_once_with([])


def test_run_index_cli_returns_2_for_unknown_action(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = run_index_cli(["bogus"])

    assert exit_code == 2
    assert "usage" in capsys.readouterr().err


def test_run_index_cli_returns_2_with_no_action(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = run_index_cli([])

    assert exit_code == 2
    assert "usage" in capsys.readouterr().err
