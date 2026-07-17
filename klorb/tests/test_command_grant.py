# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.command_grant: computing and persisting Allow/Deny grants for
BashTool's command-pattern asks. See docs/specs/bash-tool-and-command-permissions.md.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from klorb import process_config as process_config_module
from klorb.permissions.command_access import CommandRules
from klorb.permissions.command_grant import (
    _writer,
    apply_command_permission_grant,
    compute_command_grant_patterns,
)
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


# --- compute_command_grant_patterns ---


def test_falls_back_to_the_exact_argv_when_nothing_matched() -> None:
    argv = ["git", "push", "origin", "main"]
    assert compute_command_grant_patterns(CommandRules(), argv) == [argv]


def test_promotes_matched_ask_rules_own_pattern() -> None:
    command_rules = CommandRules(ask=[["git", "push", "?"]])
    assert compute_command_grant_patterns(command_rules, ["git", "push", "origin"]) == [
        ["git", "push", "?"]]


# --- _writer.apply_decision ---


def test_apply_decision_to_table_allow_appends_and_removes_ask() -> None:
    rules = CommandRules(ask=[["git", "push", "?"]])
    result = _writer.apply_decision(rules, [["git", "push", "?"]], "allow")
    assert result.allow == [["git", "push", "?"]]
    assert result.ask == []


def test_apply_decision_to_table_deny_writes_deny_not_allow() -> None:
    rules = CommandRules(ask=[["rm", "?"]])
    result = _writer.apply_decision(rules, [["rm", "?"]], "deny")
    assert result.deny == [["rm", "?"]]
    assert result.allow == []
    assert result.ask == []


def test_apply_decision_to_table_never_mutates_input_in_place() -> None:
    original_ask = [["git", "push", "?"]]
    rules = CommandRules(ask=list(original_ask))
    _writer.apply_decision(rules, [["git", "push", "?"]], "allow")
    assert rules.ask == original_ask


# --- apply_command_permission_grant: "session" scope ---


def test_session_scope_mutates_only_in_memory_session_config(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    process_config = ProcessConfig()
    argv = ["git", "status"]

    apply_command_permission_grant("allow", "session", session_config, process_config, argv)

    assert session_config.command_rules.allow == [argv]
    assert process_config.session.command_rules == CommandRules()
    assert not project_config_path(tmp_path).exists()


def test_session_scope_deny(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    argv = ["rm", "-rf", "/"]

    apply_command_permission_grant("deny", "session", session_config, None, argv)

    assert session_config.command_rules.deny == [argv]


# --- apply_command_permission_grant: "workspace" scope ---


def test_workspace_scope_mutates_session_and_process_template_and_persists(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    process_config = ProcessConfig()
    argv = ["git", "status"]

    apply_command_permission_grant("allow", "workspace", session_config, process_config, argv)

    assert session_config.command_rules.allow == [argv]
    assert process_config.session.command_rules.allow == [argv]
    session_defaults = _read_session_defaults(project_config_path(tmp_path))
    assert session_defaults["commandRules"]["allow"] == [argv]


def test_workspace_scope_promotes_matched_ask_rule_and_removes_it_from_the_same_file(
    tmp_path: Path,
) -> None:
    path = project_config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema": {"name": "klorb-config", "version": "1.0.0"},
        SESSION_DEFAULTS_KEY: {"commandRules": {"deny": [], "ask": [["git", "push", "?"]], "allow": []}},
    }), encoding="utf-8")
    session_config = SessionConfig(
        workspace=Workspace(path=tmp_path), command_rules=CommandRules(ask=[["git", "push", "?"]]))
    process_config = ProcessConfig()

    apply_command_permission_grant(
        "allow", "workspace", session_config, process_config, ["git", "push", "origin"])

    assert session_config.command_rules.ask == []
    assert session_config.command_rules.allow == [["git", "push", "?"]]
    session_defaults = _read_session_defaults(path)
    assert session_defaults["commandRules"]["ask"] == []
    assert session_defaults["commandRules"]["allow"] == [["git", "push", "?"]]


# --- apply_command_permission_grant: "homedir" scope ---


def test_homedir_scope_cleans_matching_ask_entry_out_of_workspace_file(tmp_path: Path) -> None:
    workspace_path = project_config_path(tmp_path)
    workspace_path.parent.mkdir(parents=True)
    workspace_path.write_text(json.dumps({
        "schema": {"name": "klorb-config", "version": "1.0.0"},
        SESSION_DEFAULTS_KEY: {
            "commandRules": {"deny": [], "ask": [["git", "push", "?"]], "allow": [["ls", "?"]]}},
    }), encoding="utf-8")
    session_config = SessionConfig(
        workspace=Workspace(path=tmp_path), command_rules=CommandRules(ask=[["git", "push", "?"]]))
    process_config = ProcessConfig()

    apply_command_permission_grant(
        "allow", "homedir", session_config, process_config, ["git", "push", "origin"])

    workspace_defaults = _read_session_defaults(workspace_path)
    assert workspace_defaults["commandRules"]["ask"] == []
    assert workspace_defaults["commandRules"]["allow"] == [["ls", "?"]]  # removal only, no addition
