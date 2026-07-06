# © Copyright 2026 Aaron Kimball
"""Tests for klorb.workspace.workspace_init."""

from pathlib import Path

from klorb.permissions.directory_access import DirRules
from klorb.process_config import CONFIG_SCHEMA_NAME, SESSION_DEFAULTS_KEY, project_config_path
from klorb.schema_envelope import read_versioned_json
from klorb.session import SessionConfig
from klorb.workspace import Workspace
from klorb.workspace.workspace_init import (
    write_initial_project_config,
    write_session_defaults_to_project_config,
)


def test_write_initial_project_config_untrusted_grants_read_only(tmp_path: Path) -> None:
    write_initial_project_config(tmp_path, "some/model", trusted=False)

    raw = read_versioned_json(project_config_path(tmp_path), expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = raw[SESSION_DEFAULTS_KEY]
    assert session_defaults["model"] == "some/model"
    assert session_defaults["readDirs"]["allow"] == [str(tmp_path)]
    assert session_defaults["writeDirs"]["allow"] == []


def test_write_initial_project_config_trusted_grants_write_too(tmp_path: Path) -> None:
    write_initial_project_config(tmp_path, "some/model", trusted=True)

    raw = read_versioned_json(project_config_path(tmp_path), expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = raw[SESSION_DEFAULTS_KEY]
    assert session_defaults["readDirs"]["allow"] == [str(tmp_path)]
    assert session_defaults["writeDirs"]["allow"] == [str(tmp_path)]


def test_write_initial_project_config_creates_parent_dir(tmp_path: Path) -> None:
    target = project_config_path(tmp_path)
    assert not target.parent.exists()

    write_initial_project_config(tmp_path, "some/model", trusted=False)

    assert target.is_file()


def test_write_initial_project_config_overwrites_existing_file(tmp_path: Path) -> None:
    write_initial_project_config(tmp_path, "old/model", trusted=False)
    write_initial_project_config(tmp_path, "new/model", trusted=True)

    raw = read_versioned_json(project_config_path(tmp_path), expected_schema_name=CONFIG_SCHEMA_NAME)
    assert raw[SESSION_DEFAULTS_KEY]["model"] == "new/model"
    assert raw[SESSION_DEFAULTS_KEY]["writeDirs"]["allow"] == [str(tmp_path)]


def test_write_session_defaults_dumps_scalar_fields(tmp_path: Path) -> None:
    config = SessionConfig(
        model="project/model", thinking_enabled=False, thinking_effort="low",
        max_tool_calls_per_turn=3, max_tool_calls_per_session=15, workspace=Workspace(path=tmp_path))

    write_session_defaults_to_project_config(tmp_path, config)

    raw = read_versioned_json(project_config_path(tmp_path), expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = raw[SESSION_DEFAULTS_KEY]
    assert session_defaults["model"] == "project/model"
    assert session_defaults["thinking.enabled"] is False
    assert session_defaults["thinking.effort"] == "low"
    assert session_defaults["tools.maxCallsPerTurn"] == 3
    assert session_defaults["tools.maxCallsPerSession"] == 15


def test_write_session_defaults_dumps_dir_rules_built_up_this_session(tmp_path: Path) -> None:
    ask_dir = tmp_path / "maybe"
    allow_dir = tmp_path / "yes"
    config = SessionConfig(
        workspace=Workspace(path=tmp_path),
        read_dirs=DirRules(ask=[ask_dir], allow=[allow_dir]),
        write_dirs=DirRules(deny=[tmp_path / "no"]),
    )

    write_session_defaults_to_project_config(tmp_path, config)

    raw = read_versioned_json(project_config_path(tmp_path), expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = raw[SESSION_DEFAULTS_KEY]
    assert session_defaults["readDirs"]["ask"] == [str(ask_dir)]
    assert session_defaults["readDirs"]["allow"] == [str(allow_dir)]
    assert session_defaults["writeDirs"]["deny"] == [str(tmp_path / "no")]


def test_write_session_defaults_overwrites_existing_file(tmp_path: Path) -> None:
    write_initial_project_config(tmp_path, "old/model", trusted=False)
    config = SessionConfig(model="new/model", workspace=Workspace(path=tmp_path))

    write_session_defaults_to_project_config(tmp_path, config)

    raw = read_versioned_json(project_config_path(tmp_path), expected_schema_name=CONFIG_SCHEMA_NAME)
    assert raw[SESSION_DEFAULTS_KEY]["model"] == "new/model"
