# © Copyright 2026 Aaron Kimball
"""Writes a newly-opened project's starter `.klorb/klorb-config.json` and, when a project is
trusted, dumps session defaults into it. See docs/specs/projects-and-trust.md.
"""

import logging
from pathlib import Path
from typing import Any

from klorb.permissions.directory_access import DirRules
from klorb.process_config import (
    CONFIG_SCHEMA_NAME,
    CONFIG_SCHEMA_VERSION,
    SESSION_DEFAULTS_KEY,
    SESSION_KEY_MAP,
    project_config_path,
)
from klorb.schema_envelope import write_versioned_json
from klorb.session import SessionConfig

logger = logging.getLogger(__name__)

_READ_DIRS_KEY = "readDirs"
_WRITE_DIRS_KEY = "writeDirs"

_ATTR_TO_ON_DISK_KEY: dict[str, str] = {attr: key for key, attr in SESSION_KEY_MAP.items()}
"""Inverse of `klorb.process_config.SESSION_KEY_MAP`, for `_session_defaults_json()`."""


def _dir_rules_json(rules: DirRules) -> dict[str, list[str]]:
    return {
        "deny": [str(path) for path in rules.deny],
        "ask": [str(path) for path in rules.ask],
        "allow": [str(path) for path in rules.allow],
    }


def _session_defaults_json(config: SessionConfig) -> dict[str, Any]:
    """Render every `SESSION_KEY_MAP`-recognized scalar field of `config`, plus `readDirs`/
    `writeDirs`, in on-disk `klorb-config.json` shape."""
    session_defaults: dict[str, Any] = {
        on_disk_key: getattr(config, attr) for attr, on_disk_key in _ATTR_TO_ON_DISK_KEY.items()
    }
    session_defaults[_READ_DIRS_KEY] = _dir_rules_json(config.read_dirs)
    session_defaults[_WRITE_DIRS_KEY] = _dir_rules_json(config.write_dirs)
    return session_defaults


def write_initial_project_config(workspace_root: Path, model_name: str, trusted: bool) -> None:
    """Write a starter `${workspace_root}/.klorb/klorb-config.json` for a workspace the user
    chose to open as a project. Read access to `workspace_root` is always granted; write access
    is granted only if `trusted`. Creates `.klorb/` if it doesn't exist yet; overwrites any
    existing config file at that path outright."""
    session_defaults: dict[str, Any] = {
        "model": model_name,
        _READ_DIRS_KEY: {"deny": [], "ask": [], "allow": [str(workspace_root)]},
        _WRITE_DIRS_KEY: (
            {"deny": [], "ask": [], "allow": [str(workspace_root)]} if trusted
            else {"deny": [], "ask": [], "allow": []}
        ),
    }
    logger.info("Writing initial project config file at %s", project_config_path(workspace_root))
    write_versioned_json(
        project_config_path(workspace_root), {SESSION_DEFAULTS_KEY: session_defaults},
        schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)


def write_session_defaults_to_project_config(workspace_root: Path, config: SessionConfig) -> None:
    """Write `config`'s current session defaults into `${workspace_root}/.klorb/klorb-config.json`,
    run after a previously-unregistered project is trusted so any grants the user built up during
    the session are captured. Overwrites any existing config file at that path outright."""
    write_versioned_json(
        project_config_path(workspace_root), {SESSION_DEFAULTS_KEY: _session_defaults_json(config)},
        schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)
