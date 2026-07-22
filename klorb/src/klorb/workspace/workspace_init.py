# © Copyright 2026 Aaron Kimball
"""Writes a newly-opened project's starter `.klorb/klorb-config.json` (or, later, dumps the
live session defaults a user built up before trusting the project into it) — the "Config file
initialization" and "reload... init the project config" steps of
docs/specs/projects-and-trust.md's workspace-bootstrap flow.

Like `klorb.permissions.grant`, this writes directly via `klorb.schema_envelope` rather than
through `CreateFileTool`/`EditFileTool`: it's trusted harness code reacting to an explicit
interactive decision (open this directory as a project; trust it), not the agent, so it isn't
subject to — and doesn't need to route through — the `.klorb/` self-tampering protection in
`klorb.permissions.directory_access.privileged_dirs()`.
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
"""Inverse of `klorb.process_config.SESSION_KEY_MAP`, for `_session_defaults_json()` below —
`SESSION_KEY_MAP` itself is the single source of truth for the on-disk/attribute name pairing,
so this is derived from it rather than repeating the four key strings a second time."""


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
    just chose to open as a project (`docs/specs/projects-and-trust.md`'s "Config file
    initialization" step): read access to `workspace_root` is always granted; write/create
    access is granted too, but only if `trusted` — an untrusted-but-opened project gets an
    empty `writeDirs`, leaving write access to fall through to the interactive "ask" flow (see
    docs/specs/permissions.md) rather than defaulting to allowed. `model_name` (the session's
    currently-active model at bootstrap time) is burned in as the project's own default model,
    per the spec.

    Creates `${workspace_root}/.klorb/` if it doesn't exist yet (via
    `klorb.schema_envelope.write_versioned_json`); overwrites any existing config file at that
    path outright — callers only invoke this for a workspace just confirmed to have none yet.
    """
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
    """Write `config`'s current session defaults (model, thinking settings, tool-call limits,
    `readDirs`/`writeDirs`) into `${workspace_root}/.klorb/klorb-config.json` — the "reload the
    config... offer to init the project config" step of docs/specs/projects-and-trust.md, run
    after a previously-unregistered project is trusted so any `ask`-derived grants the user
    built up during the session (before deciding to trust the project) are captured rather than
    lost the next time klorb opens this workspace. Overwrites any existing config file at that
    path outright — callers only invoke this once they've confirmed (via the user) that doing so
    is wanted, and after confirming no file exists there yet.
    """
    write_versioned_json(
        project_config_path(workspace_root), {SESSION_DEFAULTS_KEY: _session_defaults_json(config)},
        schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)
