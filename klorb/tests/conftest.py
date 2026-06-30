# © Copyright 2026 Aaron Kimball
"""Shared pytest fixtures for klorb's test suite."""

from pathlib import Path

import pytest

from klorb import logging_config


@pytest.fixture(autouse=True)
def _redirect_session_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests from writing session log files into the real KLORB_STATE_DIR."""
    monkeypatch.setattr(logging_config, "SESSION_LOGS_DIR", tmp_path / "session-logs")
