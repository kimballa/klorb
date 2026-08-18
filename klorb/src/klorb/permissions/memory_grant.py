# © Copyright 2026 Aaron Kimball
"""Computes and persists permission grants for a workspace-memory write/delete ask.

See docs/specs/memories.md.
"""

from pathlib import Path
from typing import Literal

from klorb.permissions.grant import GrantAction, GrantScope
from klorb.process_config import (
    CONFIG_SCHEMA_NAME,
    CONFIG_SCHEMA_VERSION,
    SESSION_DEFAULTS_KEY,
    ProcessConfig,
    project_config_path,
    user_config_path,
)
from klorb.schema_envelope import read_versioned_json, write_versioned_json
from klorb.session import SessionConfig

_ACCESS_FIELD: dict[str, str] = {
    "write": "memory_write_permission", "delete": "memory_delete_permission"}
_ACCESS_KEY: dict[str, str] = {
    "write": "tools.memory.writePermission", "delete": "tools.memory.deletePermission"}


def _write_file_verdict(path: Path, on_disk_key: str, action: GrantAction) -> None:
    """Write `action` into `path`'s own `sessionDefaults[on_disk_key]`, preserving every other
    key untouched. Creates `path`'s parent directory and a minimal schema envelope if `path`
    didn't exist yet."""
    raw = read_versioned_json(path, expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = dict(raw.get(SESSION_DEFAULTS_KEY, {}))
    session_defaults[on_disk_key] = action
    new_contents = dict(raw)
    new_contents[SESSION_DEFAULTS_KEY] = session_defaults
    write_versioned_json(
        path, new_contents, schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)


def apply_memory_permission_grant(
    action: GrantAction,
    scope: GrantScope,
    session_config: SessionConfig,
    process_config: ProcessConfig | None,
    access: Literal["write", "delete"],
) -> None:
    """Record a permanent Allow or Deny decision for `access` (`"write"` covers both
    `CreateMemory` and `EditMemory`; `"delete"` is `ForgetMemory`) at `scope`. There is no
    cross-file "clean a redundant ask entry" step here, unlike the rule-list grants: a scalar
    grant simply overwrites the one Verdict, leaving nothing stale behind.
    """
    field = _ACCESS_FIELD[access]
    setattr(session_config, field, action)

    if scope == "session":
        return

    if process_config is not None:
        setattr(process_config.session, field, action)

    workspace_root = session_config.workspace.path.resolve()
    on_disk_key = _ACCESS_KEY[access]
    if scope == "workspace":
        _write_file_verdict(project_config_path(workspace_root), on_disk_key, action)
    else:
        _write_file_verdict(user_config_path(), on_disk_key, action)
