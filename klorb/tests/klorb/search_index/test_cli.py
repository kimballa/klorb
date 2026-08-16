# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.cli."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from klorb.search_index import cli as cli_module
from klorb.search_index import indexer as indexer_module
from klorb.search_index.cli import run_scan_cli, run_search_cli, run_stats_cli
from klorb.search_index.embedding import EMBEDDING_DIMENSIONS
from klorb.search_index.indexer import DB_FILENAME, INDEX_DIR_NAME, ScanStats
from klorb.workspace import Workspace


class _FakeEmbeddingModel:
    def __init__(self, *, threads: int = 2, use_gpu: bool = False) -> None:
        self.threads = threads
        self.use_gpu = use_gpu

    def embed_passages(self, texts: list[str]) -> list[np.ndarray]:
        return [np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32) for _ in texts]

    def embed_query(self, text: str) -> np.ndarray:
        return np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)


@pytest.fixture(autouse=True)
def _fake_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(indexer_module, "embedding_model_available", lambda: True)
    monkeypatch.setattr(indexer_module, "get_embedding_model", lambda **kwargs: _FakeEmbeddingModel())
    monkeypatch.setattr(indexer_module, "EmbeddingModel", _FakeEmbeddingModel)
    # No real GPU in these tests regardless of what's actually installed on the machine running
    # the suite -- scan/search/stats tests exercise the CPU fallback path deterministically.
    monkeypatch.setattr(indexer_module, "try_gpu_embedding_model", lambda **kwargs: None)
    monkeypatch.setattr(cli_module, "embedding_model_available", lambda: True)


@pytest.fixture(autouse=True)
def _workspace_at_tmp_path(tmp_path: Path) -> Iterator[None]:
    with patch("klorb.search_index.cli.TrustManager") as mock_tm_cls:
        mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(path=tmp_path, trusted=True)
        yield


def test_run_search_cli_returns_1_without_embedding_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli_module, "embedding_model_available", lambda: False)

    exit_code = run_search_cli(["some query"])

    assert exit_code == 1
    assert "klorb init" in capsys.readouterr().err


def test_run_search_cli_finds_an_indexed_chunk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a.py").write_text("def debounce(fn):\n    return fn\n")

    exit_code = run_search_cli(["debounce"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "a.py" in out
    assert "debounce" in out


def test_run_search_cli_json_emits_semantic_search_shaped_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a.py").write_text("def debounce(fn):\n    return fn\n")

    exit_code = run_search_cli(["debounce", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["query"] == "debounce"
    assert len(payload["files"]) == 1
    entry = payload["files"][0]
    assert entry["filename"] == "a.py"
    assert any("debounce" in line for line in entry["lines"])


def test_run_search_cli_no_matches_reports_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_search_cli(["nothing indexed yet"])

    assert exit_code == 0
    assert "No semantic matches" in capsys.readouterr().out


def test_run_scan_cli_returns_1_without_embedding_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli_module, "embedding_model_available", lambda: False)

    exit_code = run_scan_cli([])

    assert exit_code == 1
    assert "klorb init" in capsys.readouterr().err


def test_run_scan_cli_indexes_the_workspace_and_reports_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a.py").write_text("def debounce(fn):\n    return fn\n")
    (tmp_path / "b.py").write_text("def other():\n    pass\n")

    exit_code = run_scan_cli([])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "indexed 2" in out
    assert (tmp_path / ".klorb" / INDEX_DIR_NAME / DB_FILENAME).exists()


def test_run_scan_cli_defaults_threads_to_cpu_count_and_gpu_to_on(tmp_path: Path) -> None:
    fake_indexer = MagicMock()
    fake_indexer.run_foreground_scan.return_value = ScanStats(
        files_scanned=0, files_indexed=0, files_removed=0, chunks_indexed=0, elapsed_seconds=0.0)
    with patch("klorb.search_index.cli.WorkspaceIndexer", return_value=fake_indexer):
        with patch("klorb.search_index.cli.os.cpu_count", return_value=7):
            run_scan_cli([])

    fake_indexer.run_foreground_scan.assert_called_once_with(
        rebuild=False, num_threads=7, use_gpu=True)


def test_run_scan_cli_passes_threads_and_rebuild_flags(tmp_path: Path) -> None:
    fake_indexer = MagicMock()
    fake_indexer.run_foreground_scan.return_value = ScanStats(
        files_scanned=0, files_indexed=0, files_removed=0, chunks_indexed=0, elapsed_seconds=0.0)
    with patch("klorb.search_index.cli.WorkspaceIndexer", return_value=fake_indexer):
        run_scan_cli(["--threads", "3", "--rebuild"])

    fake_indexer.run_foreground_scan.assert_called_once_with(
        rebuild=True, num_threads=3, use_gpu=True)


def test_run_scan_cli_gpu_default_reflects_config_false(tmp_path: Path) -> None:
    config_path = tmp_path / ".klorb" / "klorb-config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({
        "schema": {"name": "klorb-config", "version": "1.0.0"},
        "sessionDefaults": {"search.indexer.gpuEnabled": False},
    }))
    fake_indexer = MagicMock()
    fake_indexer.run_foreground_scan.return_value = ScanStats(
        files_scanned=0, files_indexed=0, files_removed=0, chunks_indexed=0, elapsed_seconds=0.0)
    with patch("klorb.search_index.cli.WorkspaceIndexer", return_value=fake_indexer):
        run_scan_cli([])

    fake_indexer.run_foreground_scan.assert_called_once_with(
        rebuild=False, num_threads=os.cpu_count() or 1, use_gpu=False)


def test_run_scan_cli_explicit_gpu_flag_overrides_config_default(tmp_path: Path) -> None:
    config_path = tmp_path / ".klorb" / "klorb-config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({
        "schema": {"name": "klorb-config", "version": "1.0.0"},
        "sessionDefaults": {"search.indexer.gpuEnabled": False},
    }))
    fake_indexer = MagicMock()
    fake_indexer.run_foreground_scan.return_value = ScanStats(
        files_scanned=0, files_indexed=0, files_removed=0, chunks_indexed=0, elapsed_seconds=0.0)
    with patch("klorb.search_index.cli.WorkspaceIndexer", return_value=fake_indexer):
        run_scan_cli(["--gpu"])

    fake_indexer.run_foreground_scan.assert_called_once_with(
        rebuild=False, num_threads=os.cpu_count() or 1, use_gpu=True)


def test_run_scan_cli_no_gpu_flag_forces_cpu(tmp_path: Path) -> None:
    fake_indexer = MagicMock()
    fake_indexer.run_foreground_scan.return_value = ScanStats(
        files_scanned=0, files_indexed=0, files_removed=0, chunks_indexed=0, elapsed_seconds=0.0)
    with patch("klorb.search_index.cli.WorkspaceIndexer", return_value=fake_indexer):
        with patch("klorb.search_index.cli.os.cpu_count", return_value=7):
            run_scan_cli(["--no-gpu"])

    fake_indexer.run_foreground_scan.assert_called_once_with(
        rebuild=False, num_threads=7, use_gpu=False)


def test_run_scan_cli_returns_1_when_index_owned_elsewhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    fake_indexer = MagicMock()
    fake_indexer.run_foreground_scan.side_effect = RuntimeError("owned by another running klorb process")
    with patch("klorb.search_index.cli.WorkspaceIndexer", return_value=fake_indexer):
        exit_code = run_scan_cli([])

    assert exit_code == 1
    assert "owned by another running klorb process" in capsys.readouterr().err


def test_run_stats_cli_returns_1_without_an_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = run_stats_cli([])

    assert exit_code == 1
    assert "klorb index scan" in capsys.readouterr().out


def test_run_stats_cli_reports_counts_after_a_scan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a.py").write_text("def debounce(fn):\n    return fn\n")
    run_scan_cli([])
    capsys.readouterr()  # discard scan output

    exit_code = run_stats_cli([])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Files indexed:   1" in out
    assert "Chunks indexed:" in out
    assert "Index size:" in out


def test_run_stats_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "a.py").write_text("def debounce(fn):\n    return fn\n")
    run_scan_cli([])
    capsys.readouterr()

    exit_code = run_stats_cli(["--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["file_count"] == 1
    assert payload["chunk_count"] > 0
    assert "db_size_mb" in payload
