# © Copyright 2026 Aaron Kimball
"""Tests for klorb.paths."""

import importlib
from pathlib import Path

import pytest

from klorb import paths


def test_default_config_dir() -> None:
    assert paths.KLORB_CONFIG_DIR == Path.home() / ".config" / "klorb"


def test_default_data_dir() -> None:
    assert paths.KLORB_DATA_DIR == Path.home() / ".local" / "share" / "klorb"


def test_default_state_dir() -> None:
    assert paths.KLORB_STATE_DIR == Path.home() / ".local" / "state" / "klorb"


def test_session_logs_dir_is_under_state_dir() -> None:
    assert paths.SESSION_LOGS_DIR == paths.KLORB_STATE_DIR / "session-logs"


def test_env_var_overrides_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.KLORB_CONFIG_DIR_ENV_VAR, str(tmp_path))
    importlib.reload(paths)
    try:
        assert paths.KLORB_CONFIG_DIR == tmp_path
    finally:
        monkeypatch.undo()
        importlib.reload(paths)


def test_env_var_overrides_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.KLORB_DATA_DIR_ENV_VAR, str(tmp_path))
    importlib.reload(paths)
    try:
        assert paths.KLORB_DATA_DIR == tmp_path
    finally:
        monkeypatch.undo()
        importlib.reload(paths)


def test_env_var_overrides_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.KLORB_STATE_DIR_ENV_VAR, str(tmp_path))
    importlib.reload(paths)
    try:
        assert paths.KLORB_STATE_DIR == tmp_path
        assert paths.SESSION_LOGS_DIR == tmp_path / "session-logs"
    finally:
        monkeypatch.undo()
        importlib.reload(paths)


def test_env_var_override_with_tilde_expands_to_home_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(paths.KLORB_CONFIG_DIR_ENV_VAR, "~/custom-klorb-config")
    importlib.reload(paths)
    try:
        assert paths.KLORB_CONFIG_DIR == home / "custom-klorb-config"
    finally:
        monkeypatch.undo()
        importlib.reload(paths)
