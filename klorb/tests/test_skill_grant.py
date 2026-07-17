# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.skill_grant: computing and persisting Allow/Deny grants for
ActivateSkill's (namespace, name) asks. See docs/specs/skills.md."""

from pathlib import Path
from typing import Any

import pytest

from klorb import process_config as process_config_module
from klorb.permissions.skill_access import SkillRules
from klorb.permissions.skill_grant import _writer, apply_skill_permission_grant
from klorb.process_config import CONFIG_SCHEMA_NAME, SESSION_DEFAULTS_KEY, ProcessConfig, project_config_path
from klorb.schema_envelope import read_versioned_json
from klorb.session import SessionConfig
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _isolate_homedir_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(process_config_module, "KLORB_CONFIG_DIR", tmp_path / "homedir")


def _read_session_defaults(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = read_versioned_json(
        path, expected_schema_name=CONFIG_SCHEMA_NAME).get(SESSION_DEFAULTS_KEY, {})
    return result


def _session_config(tmp_path: Path) -> SessionConfig:
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    return SessionConfig(workspace=Workspace(path=ws, trusted=True))


# --- _writer.apply_decision ---


def test_apply_decision_allow_appends_and_removes_ask() -> None:
    rules = SkillRules(ask=[("internal", "s")])
    result = _writer.apply_decision(rules, [("internal", "s")], "allow")
    assert result.allow == [("internal", "s")]
    assert result.ask == []


def test_apply_decision_deny_writes_deny_leaves_allow() -> None:
    rules = SkillRules(allow=[("internal", "other")])
    result = _writer.apply_decision(rules, [("internal", "s")], "deny")
    assert result.deny == [("internal", "s")]
    assert result.allow == [("internal", "other")]


def test_apply_decision_dedups() -> None:
    rules = SkillRules(allow=[("internal", "s")])
    result = _writer.apply_decision(rules, [("internal", "s")], "allow")
    assert result.allow == [("internal", "s")]


def test_apply_decision_never_mutates_input() -> None:
    rules = SkillRules(ask=[("internal", "s")])
    _writer.apply_decision(rules, [("internal", "s")], "allow")
    assert rules.ask == [("internal", "s")]


# --- apply_skill_permission_grant scopes ---


def test_session_scope_updates_live_config_only(tmp_path: Path) -> None:
    session_config = _session_config(tmp_path)
    apply_skill_permission_grant("allow", "session", session_config, None, ("internal", "s"))
    assert session_config.skill_rules.allow == [("internal", "s")]
    # Nothing persisted.
    assert not project_config_path(session_config.workspace.path).exists()


def test_workspace_scope_persists_to_project_file(tmp_path: Path) -> None:
    session_config = _session_config(tmp_path)
    process_config = ProcessConfig()
    apply_skill_permission_grant(
        "allow", "workspace", session_config, process_config, ("workspace", "s"))
    assert session_config.skill_rules.allow == [("workspace", "s")]
    assert process_config.session.skill_rules.allow == [("workspace", "s")]
    defaults = _read_session_defaults(project_config_path(session_config.workspace.path))
    assert defaults["skillRules"]["allow"] == ["workspace/s"]


def test_homedir_scope_persists_to_user_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_config = _session_config(tmp_path)
    apply_skill_permission_grant("allow", "homedir", session_config, None, ("user", "s"))
    user_file = tmp_path / "homedir" / "klorb-config.json"
    defaults = _read_session_defaults(user_file)
    assert defaults["skillRules"]["allow"] == ["user/s"]


def test_homedir_scope_cleans_redundant_workspace_ask(tmp_path: Path) -> None:
    session_config = _session_config(tmp_path)
    process_config = ProcessConfig()
    # Seed a workspace-file ask entry, then grant homedir-scope for the same pair.
    apply_skill_permission_grant(
        "allow", "workspace", session_config, process_config, ("internal", "s"))
    # Put an ask entry into the workspace file to be cleaned.
    project_file = project_config_path(session_config.workspace.path)
    raw = read_versioned_json(project_file, expected_schema_name=CONFIG_SCHEMA_NAME)
    raw[SESSION_DEFAULTS_KEY]["skillRules"]["ask"] = ["internal/t"]
    from klorb.schema_envelope import write_versioned_json
    write_versioned_json(project_file, raw, schema_name=CONFIG_SCHEMA_NAME, schema_version="1.0.0")

    apply_skill_permission_grant("allow", "homedir", session_config, process_config, ("internal", "t"))
    defaults = _read_session_defaults(project_file)
    assert "internal/t" not in defaults["skillRules"]["ask"]
