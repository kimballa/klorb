# © Copyright 2026 Aaron Kimball
"""Shared fixtures for the klorb.cli test tree."""

from pathlib import Path

import pytest

from klorb import token_estimate as token_estimate_module
from klorb.workspace import trust_manager as trust_manager_module


@pytest.fixture(autouse=True)
def _isolate_projects_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `klorb.workspace.trust_manager.projects_path()` (and so any real `TrustManager()`
    a subcommand constructs) and `klorb.token_estimate.tiktoken_cache_target_dir()` (and so any
    `configure_tiktoken_cache_env()` call) at an empty location under `tmp_path`, so no test in
    this tree reads or writes the developer's own `$KLORB_DATA_DIR/projects.json` or
    `$KLORB_DATA_DIR/tiktoken-cache/`."""
    monkeypatch.setattr(trust_manager_module, "get_klorb_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(token_estimate_module, "get_klorb_data_dir", lambda: tmp_path / "data")
