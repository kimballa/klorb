# © Copyright 2026 Aaron Kimball
"""Tests for klorb.paths."""

from pathlib import Path

import pytest

from klorb import paths


def test_default_config_dir() -> None:
    assert paths.get_klorb_config_dir() == Path.home() / ".config" / "klorb"


def test_default_data_dir() -> None:
    assert paths.get_klorb_data_dir() == Path.home() / ".local" / "share" / "klorb"


def test_default_state_dir() -> None:
    assert paths.get_klorb_state_dir() == Path.home() / ".local" / "state" / "klorb"


def test_session_logs_dir_is_under_state_dir() -> None:
    assert paths.get_session_logs_dir() == paths.get_klorb_state_dir() / "session-logs"


def test_env_var_overrides_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.KLORB_CONFIG_DIR_ENV_VAR, str(tmp_path))
    assert paths.get_klorb_config_dir() == tmp_path


def test_env_var_overrides_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.KLORB_DATA_DIR_ENV_VAR, str(tmp_path))
    assert paths.get_klorb_data_dir() == tmp_path


def test_env_var_overrides_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.KLORB_STATE_DIR_ENV_VAR, str(tmp_path))
    assert paths.get_klorb_state_dir() == tmp_path
    assert paths.get_session_logs_dir() == tmp_path / "session-logs"


def test_env_var_override_with_tilde_expands_to_home_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(paths.KLORB_CONFIG_DIR_ENV_VAR, "~/custom-klorb-config")
    assert paths.get_klorb_config_dir() == home / "custom-klorb-config"
