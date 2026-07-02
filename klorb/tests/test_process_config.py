# © Copyright 2026 Aaron Kimball
"""Tests for klorb.process_config."""

import json
import logging
from pathlib import Path

import pytest

from klorb import process_config as process_config_module
from klorb.openrouter import DEFAULT_MODEL
from klorb.openrouter import OPENROUTER_BASE_URL
from klorb.process_config import DEFAULT_PROMPT_INPUT_MAX_LINES
from klorb.process_config import DEFAULT_READ_FILE_MAX_LINES
from klorb.process_config import load_process_config
from klorb.session import DEFAULT_MAX_TOOL_CALLS_PER_SESSION
from klorb.session import DEFAULT_MAX_TOOL_CALLS_PER_TURN
from klorb.session import THINKING_EFFORT_TOKEN_BUDGETS
from klorb.session import SessionConfig


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"schema": {"name": "klorb-config", "version": "1.0.0"}, **data}
    path.write_text(json.dumps(envelope), encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_config_layers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point every file-backed config layer at empty locations under `tmp_path` by default,
    so tests never accidentally read a real `/etc/klorb/klorb-config.json` or the
    developer's own `~/.config/klorb/klorb-config.json`.
    """
    monkeypatch.setenv(
        process_config_module.KLORB_ETC_CONFIG_ENV_VAR, str(tmp_path / "etc" / "klorb-config.json"))
    monkeypatch.setattr(process_config_module, "KLORB_CONFIG_DIR", tmp_path / "user-config")


def test_defaults_when_no_config_files_exist(tmp_path: Path) -> None:
    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session == SessionConfig()
    assert process_config.session.model == DEFAULT_MODEL
    assert process_config.session.max_tool_calls_per_turn == DEFAULT_MAX_TOOL_CALLS_PER_TURN
    assert process_config.session.max_tool_calls_per_session == DEFAULT_MAX_TOOL_CALLS_PER_SESSION
    assert process_config.prompt_input_max_lines == DEFAULT_PROMPT_INPUT_MAX_LINES
    assert process_config.thinking_token_budgets == THINKING_EFFORT_TOKEN_BUDGETS
    assert process_config.read_file_max_lines == DEFAULT_READ_FILE_MAX_LINES
    assert process_config.openrouter_base_url == OPENROUTER_BASE_URL


def test_etc_config_path_honors_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom_etc_path = tmp_path / "custom-etc.json"
    _write_config(custom_etc_path, {"sessionDefaults": {"model": "etc/model"}})
    monkeypatch.setenv(process_config_module.KLORB_ETC_CONFIG_ENV_VAR, str(custom_etc_path))

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.model == "etc/model"


def test_process_only_fields_are_overridable_via_config_file(tmp_path: Path) -> None:
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {
            "terminal.input.maxLines": 20,
            "thinking.tokenBudgets": {"low": 1_000, "medium": 2_000, "high": 3_000},
            "tools.readFile.maxLines": 500,
            "providers.openrouter.baseUrl": "https://gateway.example.com/v1",
        },
    )

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.prompt_input_max_lines == 20
    assert process_config.thinking_token_budgets == {"low": 1_000, "medium": 2_000, "high": 3_000}
    assert process_config.read_file_max_lines == 500
    assert process_config.openrouter_base_url == "https://gateway.example.com/v1"


def test_session_defaults_are_nested_under_one_key(tmp_path: Path) -> None:
    """Session-scoped settings live under `sessionDefaults`, not flattened alongside
    process-only keys — the on-disk file mirrors `ProcessConfig` nesting `session:
    SessionConfig`.
    """
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {
            "sessionDefaults": {
                "model": "project/model",
                "thinking.enabled": False,
                "thinking.effort": "low",
                "tools.maxCallsPerTurn": 3,
                "tools.maxCallsPerSession": 15,
            },
        },
    )

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.model == "project/model"
    assert process_config.session.thinking_enabled is False
    assert process_config.session.thinking_effort == "low"
    assert process_config.session.max_tool_calls_per_turn == 3
    assert process_config.session.max_tool_calls_per_session == 15


def test_interactive_is_not_a_recognized_session_default(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """`interactive` is always inferred from CLI flags, never config-file-driven."""
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"sessionDefaults": {"interactive": False}})

    with caplog.at_level(logging.WARNING, logger="klorb.process_config"):
        process_config = load_process_config(cwd=tmp_path)

    assert process_config.session.interactive is True
    assert "interactive" in caplog.text


def test_unrecognized_config_key_is_dropped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"totally.madeUp": "value"})

    with caplog.at_level(logging.WARNING, logger="klorb.process_config"):
        process_config = load_process_config(cwd=tmp_path)

    assert process_config.session.model == DEFAULT_MODEL
    assert "totally.madeUp" in caplog.text


def test_project_config_file_overrides_defaults(tmp_path: Path) -> None:
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"sessionDefaults": {"model": "project/model"}})

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.model == "project/model"


def test_user_config_overrides_etc_config(tmp_path: Path) -> None:
    _write_config(tmp_path / "etc" / "klorb-config.json", {"sessionDefaults": {"model": "etc/model"}})
    _write_config(
        tmp_path / "user-config" / "klorb-config.json", {"sessionDefaults": {"model": "user/model"}})

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.model == "user/model"


def test_project_config_overrides_user_config(tmp_path: Path) -> None:
    _write_config(
        tmp_path / "user-config" / "klorb-config.json", {"sessionDefaults": {"model": "user/model"}})
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"sessionDefaults": {"model": "project/model"}})

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.model == "project/model"


def test_config_flag_path_overrides_project_config(tmp_path: Path) -> None:
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"sessionDefaults": {"model": "project/model"}})
    flag_path = tmp_path / "extra-config.json"
    _write_config(flag_path, {"sessionDefaults": {"model": "flag/model"}})

    process_config = load_process_config(config_flag_path=flag_path, cwd=tmp_path)
    assert process_config.session.model == "flag/model"


def test_config_layers_merge_rather_than_replace_wholesale(tmp_path: Path) -> None:
    """A later layer overriding one key shouldn't reset keys only set by an earlier layer,
    in either the top-level object or the nested `sessionDefaults` object.
    """
    _write_config(
        tmp_path / "user-config" / "klorb-config.json",
        {
            "sessionDefaults": {"model": "user/model", "thinking.enabled": False},
            "terminal.input.maxLines": 20,
        },
    )
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"sessionDefaults": {"model": "project/model"}})

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.model == "project/model"
    assert process_config.session.thinking_enabled is False
    assert process_config.prompt_input_max_lines == 20


def test_config_file_with_mismatched_schema_name_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / ".klorb" / "klorb-config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema": {"name": "klorb-session", "version": "1.0.0"},
            "sessionDefaults": {"model": "wrong/model"},
        }),
        encoding="utf-8")

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.model == DEFAULT_MODEL
