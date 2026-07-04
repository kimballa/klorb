# © Copyright 2026 Aaron Kimball
"""Tests for klorb.process_config."""

import json
import logging
from pathlib import Path

import pytest

from klorb import process_config as process_config_module
from klorb.openrouter import DEFAULT_MODEL
from klorb.openrouter import OPENROUTER_BASE_URL
from klorb.permissions.directory_access import DirectoryAccessTable
from klorb.process_config import DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS
from klorb.process_config import DEFAULT_FIND_FILE_MAX_RESULTS
from klorb.process_config import DEFAULT_GREP_MAX_RESULTS
from klorb.process_config import DEFAULT_PROMPT_INPUT_MAX_LINES
from klorb.process_config import DEFAULT_READ_FILE_MAX_LINES
from klorb.process_config import DEFAULT_SHELL_COMMAND
from klorb.process_config import PROCESS_KEY_MAP
from klorb.process_config import SESSION_KEY_MAP
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
    # workspace_root is always set from find_workspace_root(cwd), not SessionConfig's own
    # Path.cwd() default_factory — see test_workspace_root_falls_back_to_cwd_when_no_klorb_dir_found.
    assert process_config.session == SessionConfig(workspace_root=tmp_path)
    assert process_config.session.model == DEFAULT_MODEL
    assert process_config.session.max_tool_calls_per_turn == DEFAULT_MAX_TOOL_CALLS_PER_TURN
    assert process_config.session.max_tool_calls_per_session == DEFAULT_MAX_TOOL_CALLS_PER_SESSION
    assert process_config.prompt_input_max_lines == DEFAULT_PROMPT_INPUT_MAX_LINES
    assert process_config.thinking_token_budgets == THINKING_EFFORT_TOKEN_BUDGETS
    assert process_config.read_file_max_lines == DEFAULT_READ_FILE_MAX_LINES
    assert process_config.edit_file_drift_search_radius == DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS
    assert process_config.grep_max_results == DEFAULT_GREP_MAX_RESULTS
    assert process_config.find_file_max_results == DEFAULT_FIND_FILE_MAX_RESULTS
    assert process_config.openrouter_base_url == OPENROUTER_BASE_URL
    assert process_config.shell_command == DEFAULT_SHELL_COMMAND
    assert process_config.shell_timeout_seconds is None
    assert process_config.is_workspace_trusted is False
    assert process_config.compatibility_claude_markdown is False


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
            "tools.editFile.driftSearchRadius": 5,
            "tools.grep.maxResults": 50,
            "tools.findFile.maxResults": 75,
            "providers.openrouter.baseUrl": "https://gateway.example.com/v1",
            "shell.command": "/bin/zsh",
            "shell.timeout": 30,
        },
    )

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.prompt_input_max_lines == 20
    assert process_config.thinking_token_budgets == {"low": 1_000, "medium": 2_000, "high": 3_000}
    assert process_config.read_file_max_lines == 500
    assert process_config.edit_file_drift_search_radius == 5
    assert process_config.grep_max_results == 50
    assert process_config.find_file_max_results == 75
    assert process_config.openrouter_base_url == "https://gateway.example.com/v1"
    assert process_config.shell_command == "/bin/zsh"
    assert process_config.shell_timeout_seconds == 30


def test_compatibility_claude_markdown_is_overridable_via_config_file(tmp_path: Path) -> None:
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {"compatibility.claudeMarkdown": True},
    )

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.compatibility_claude_markdown is True


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


def test_readdirs_and_writedirs_concatenate_across_layers(tmp_path: Path) -> None:
    """See docs/specs/permissions.md: a later layer's readDirs/writeDirs entries add to,
    rather than replace, every earlier layer's — unlike every other config key."""
    _write_config(
        tmp_path / "user-config" / "klorb-config.json",
        {"sessionDefaults": {"readDirs": {"allow": ["/a", "/b", "/c"]}}})
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {"sessionDefaults": {"readDirs": {"allow": ["/d", "/e", "/f"]}}})

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.read_dirs.allow == [
        Path(p) for p in ["/a", "/b", "/c", "/d", "/e", "/f"]]


def test_readdirs_writedirs_merge_concatenates_per_category_independently(tmp_path: Path) -> None:
    """A layer contributing only `deny` and another contributing only `allow` for the same
    direction must not clobber each other's category."""
    _write_config(
        tmp_path / "user-config" / "klorb-config.json",
        {"sessionDefaults": {"writeDirs": {"deny": ["/nope"]}}})
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {"sessionDefaults": {"writeDirs": {"allow": ["/yolo"]}}})

    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.write_dirs.deny == [Path("/nope")]
    assert process_config.session.write_dirs.allow == [Path("/yolo")]


def test_readdirs_writedirs_duplicates_and_conflicts_across_layers_are_tolerated(
    tmp_path: Path,
) -> None:
    """Concatenation must never deduplicate or reject conflicting entries — conflicts are
    resolved later at evaluation time via category order, not at merge time."""
    _write_config(
        tmp_path / "user-config" / "klorb-config.json",
        {"sessionDefaults": {"readDirs": {"allow": ["/shared"]}}})
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {"sessionDefaults": {"readDirs": {"deny": ["/shared"]}}})

    process_config = load_process_config(cwd=tmp_path)  # must not raise
    assert process_config.session.read_dirs.allow == [Path("/shared")]
    assert process_config.session.read_dirs.deny == [Path("/shared")]


def test_readdirs_writedirs_bypass_session_key_map_routing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """readDirs/writeDirs must be popped out and handled bespoke before _route_keys() ever
    sees them — a regression here would either drop the settings silently or spam a false
    "unrecognized key" warning."""
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json", {"sessionDefaults": {"readDirs": {"allow": ["/a"]}}})

    with caplog.at_level(logging.WARNING, logger="klorb.process_config"):
        process_config = load_process_config(cwd=tmp_path)

    assert process_config.session.read_dirs.allow == [Path("/a")]
    assert "readDirs" not in caplog.text


def test_session_key_map_deliberately_excludes_readdirs_and_writedirs() -> None:
    """Deep-inspect SESSION_KEY_MAP itself, not just load_process_config()'s observable
    behavior (test_readdirs_writedirs_bypass_session_key_map_routing above already covers
    that): readDirs/writeDirs must never appear here, on either side of the mapping, because
    they're merged by concatenation, not _route_keys()'s 1:1 scalar replacement — see
    docs/specs/permissions.md. If this assertion ever fails, the fix is almost certainly to
    remove the accidental entry, not to update this test — re-read the spec first.
    """
    assert "readDirs" not in SESSION_KEY_MAP
    assert "writeDirs" not in SESSION_KEY_MAP
    assert "read_dirs" not in SESSION_KEY_MAP.values()
    assert "write_dirs" not in SESSION_KEY_MAP.values()


def test_is_workspace_trusted_has_no_on_disk_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """is_workspace_trusted must never be settable from klorb-config.json — a project must not
    be able to grant itself trust via its own config file."""
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"isWorkspaceTrusted": True})

    with caplog.at_level(logging.WARNING, logger="klorb.process_config"):
        process_config = load_process_config(cwd=tmp_path)

    assert process_config.is_workspace_trusted is False
    assert "isWorkspaceTrusted" in caplog.text


def test_process_key_map_deliberately_excludes_is_workspace_trusted() -> None:
    """Deep-inspect PROCESS_KEY_MAP itself, not just load_process_config()'s observable
    behavior (test_is_workspace_trusted_has_no_on_disk_key above already covers that): a
    static, structural check that catches the mistake at the source (the map definition)
    rather than only downstream, and fails with a clear message pointing at *why* even if a
    future change accidentally makes the behavioral test pass some other way.

    See docs/adrs/gate-read-hard-boundary-on-workspace-trust.md: "ProcessConfig.is_workspace_trusted:
    bool field, default False, deliberately absent from PROCESS_KEY_MAP" — a project config
    file granting itself trust would defeat the entire point of gating ReadFile's hard
    workspace-root boundary on this flag. If this assertion ever fails, do not "fix" it by
    updating the test — re-read the ADR; the fix is almost certainly to remove whatever entry
    was just added to PROCESS_KEY_MAP.
    """
    assert "is_workspace_trusted" not in PROCESS_KEY_MAP.values()
    assert "isWorkspaceTrusted" not in PROCESS_KEY_MAP


def test_workspace_root_uses_find_workspace_root_result(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".klorb").mkdir(parents=True)
    nested = project / "src" / "sub"
    nested.mkdir(parents=True)

    process_config = load_process_config(cwd=nested)
    assert process_config.session.workspace_root == project


def test_workspace_root_falls_back_to_cwd_when_no_klorb_dir_found(tmp_path: Path) -> None:
    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.workspace_root == tmp_path


def test_stricter_layer_always_wins_end_to_end(tmp_path: Path) -> None:
    """The core security invariant (docs/specs/permissions.md): a deny from the
    lowest-precedence layer (/etc) can never be overridden by an allow from a
    higher-precedence layer (project), because lists are concatenated and evaluation is by
    category order, not layer order."""
    sensitive = tmp_path / "sensitive"
    sensitive.mkdir()
    _write_config(
        tmp_path / "etc" / "klorb-config.json",
        {"sessionDefaults": {"writeDirs": {"deny": [str(sensitive)]}}})
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {"sessionDefaults": {"writeDirs": {"allow": [str(tmp_path)]}}})

    process_config = load_process_config(cwd=tmp_path)
    table = DirectoryAccessTable(process_config.session.write_dirs, process_config.session.workspace_root)
    assert table.evaluate(sensitive / "key.txt") == "deny"
