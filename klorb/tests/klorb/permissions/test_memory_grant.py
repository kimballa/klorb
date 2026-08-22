# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.memory_grant: computing and persisting Allow/Deny grants for a
workspace-memory write/delete ask. See docs/specs/memories.md."""

from pathlib import Path
from typing import Any

import pytest

from klorb import process_config as process_config_module
from klorb.permissions.memory_grant import apply_memory_permission_grant
from klorb.process_config import CONFIG_SCHEMA_NAME, SESSION_DEFAULTS_KEY, ProcessConfig, project_config_path
from klorb.schema_envelope import read_versioned_json
from klorb.session import SessionConfig, WorkspaceAccess
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _isolate_homedir_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(process_config_module, "get_klorb_config_dir", lambda: tmp_path / "homedir")


def _read_session_defaults(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = read_versioned_json(
        path, expected_schema_name=CONFIG_SCHEMA_NAME).get(SESSION_DEFAULTS_KEY, {})
    return result


def _session_config(tmp_path: Path) -> SessionConfig:
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    return SessionConfig(workspace_access=WorkspaceAccess(workspace=Workspace(path=ws, trusted=True)))


def test_session_scope_updates_live_config_only(tmp_path: Path) -> None:
    session_config = _session_config(tmp_path)
    apply_memory_permission_grant("allow", "session", session_config, None, "write")
    assert session_config.memory_write_permission == "allow"
    # Nothing persisted.
    assert not project_config_path(session_config.workspace.path).exists()


def test_workspace_scope_persists_to_project_file(tmp_path: Path) -> None:
    session_config = _session_config(tmp_path)
    process_config = ProcessConfig()
    apply_memory_permission_grant("allow", "workspace", session_config, process_config, "write")
    assert session_config.memory_write_permission == "allow"
    assert process_config.session.memory_write_permission == "allow"
    defaults = _read_session_defaults(project_config_path(session_config.workspace.path))
    assert defaults["tools.memory.writePermission"] == "allow"


def test_homedir_scope_persists_to_user_file(tmp_path: Path) -> None:
    session_config = _session_config(tmp_path)
    apply_memory_permission_grant("deny", "homedir", session_config, None, "delete")
    user_file = tmp_path / "homedir" / "klorb-config.json"
    defaults = _read_session_defaults(user_file)
    assert defaults["tools.memory.deletePermission"] == "deny"


def test_write_and_delete_grants_are_independent(tmp_path: Path) -> None:
    session_config = _session_config(tmp_path)
    apply_memory_permission_grant("allow", "session", session_config, None, "write")
    assert session_config.memory_write_permission == "allow"
    assert session_config.memory_delete_permission == "ask"


def test_workspace_grant_preserves_other_session_defaults_keys(tmp_path: Path) -> None:
    session_config = _session_config(tmp_path)
    process_config = ProcessConfig()
    apply_memory_permission_grant("allow", "workspace", session_config, process_config, "write")
    apply_memory_permission_grant("deny", "workspace", session_config, process_config, "delete")

    defaults = _read_session_defaults(project_config_path(session_config.workspace.path))
    assert defaults["tools.memory.writePermission"] == "allow"
    assert defaults["tools.memory.deletePermission"] == "deny"
