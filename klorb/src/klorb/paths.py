# © Copyright 2026 Aaron Kimball
"""Filesystem locations klorb uses for config, data, and state, with env var overrides."""

import os
from pathlib import Path

KLORB_CONFIG_DIR_ENV_VAR = "KLORB_CONFIG_DIR"
KLORB_DATA_DIR_ENV_VAR = "KLORB_DATA_DIR"
KLORB_STATE_DIR_ENV_VAR = "KLORB_STATE_DIR"

DEFAULT_KLORB_CONFIG_DIR = Path.home() / ".config" / "klorb"
DEFAULT_KLORB_DATA_DIR = Path.home() / ".local" / "share" / "klorb"
DEFAULT_KLORB_STATE_DIR = Path.home() / ".local" / "state" / "klorb"

SESSION_LOGS_DIR_NAME = "session-logs"


def _dir_from_env(env_var: str, default: Path) -> Path:
    """Resolve a klorb directory from an env var override, falling back to the given default."""
    override = os.environ.get(env_var)
    return Path(override) if override else default


KLORB_CONFIG_DIR: Path = _dir_from_env(KLORB_CONFIG_DIR_ENV_VAR, DEFAULT_KLORB_CONFIG_DIR)
KLORB_DATA_DIR: Path = _dir_from_env(KLORB_DATA_DIR_ENV_VAR, DEFAULT_KLORB_DATA_DIR)
KLORB_STATE_DIR: Path = _dir_from_env(KLORB_STATE_DIR_ENV_VAR, DEFAULT_KLORB_STATE_DIR)

SESSION_LOGS_DIR: Path = KLORB_STATE_DIR / SESSION_LOGS_DIR_NAME
