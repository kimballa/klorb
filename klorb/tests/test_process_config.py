# © Copyright 2026 Aaron Kimball
"""Tests for klorb.process_config."""

import json
import logging
from pathlib import Path

import pytest

from klorb import process_config as process_config_module
from klorb.openrouter import DEFAULT_MODEL, OPENROUTER_BASE_URL
from klorb.permissions.directory_access import DirectoryAccessTable
from klorb.process_config import (
    DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS,
    DEFAULT_FIND_FILE_MAX_RESULTS,
    DEFAULT_GREP_MAX_RESULTS,
    DEFAULT_PROMPT_INPUT_MAX_LINES,
    DEFAULT_READ_FILE_MAX_LINES,
    DEFAULT_SHELL_COMMAND,
    PROCESS_KEY_MAP,
    SESSION_KEY_MAP,
    load_process_config,
    persist_theme,
    user_config_path,
)
from klorb.session import (
    DEFAULT_MAX_TOOL_CALLS_PER_SESSION,
    DEFAULT_MAX_TOOL_CALLS_PER_TURN,
    THINKING_EFFORT_TOKEN_BUDGETS,
    SessionConfig,
)
from klorb.workspace import Workspace


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"schema": {"name": "klorb-config", "version": "1.0.0"}, **data}
    path.write_text(json.dumps(envelope), encoding="utf-8")


def _trusted_workspace(path: Path) -> Workspace:
    """A `Workspace` for `path`, trusted — passed to `load_process_config(workspace=...)` by
    every test below that writes a `.klorb/klorb-config.json` and expects it to be merged as
    layer 4: since docs/specs/projects-and-trust.md, that layer is skipped entirely unless
    `workspace.trusted`, so these tests (which are about the merge mechanism itself, not trust
    gating — see `test_untrusted_workspace_skips_project_config_layer` for that) opt in
    explicitly rather than relying on the conservative untrusted default."""
    return Workspace(path=path, trusted=True)


_real_default_config_layer = process_config_module._default_config_layer
"""A handle to the genuine, packaged-resource-reading `_default_config_layer`, captured at
collection time before `_isolate_config_layers` (below) monkeypatches the module attribute of
the same name for every test. The `test_default_config_layer_*` tests restore it explicitly
to exercise the real packaged `default-config.json`."""


@pytest.fixture(autouse=True)
def _isolate_config_layers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point every file-backed config layer at empty locations under `tmp_path`, and blank out
    the packaged built-in-defaults layer, so tests never accidentally read a real
    `/etc/klorb/klorb-config.json`, the developer's own `~/.config/klorb/klorb-config.json`,
    or the packaged `default-config.json`'s own `readDirs`/session-default values. Tests that
    specifically cover the packaged layer (see `test_default_config_layer_*` below) restore
    the real `_default_config_layer` themselves.
    """
    monkeypatch.setenv(
        process_config_module.KLORB_ETC_CONFIG_ENV_VAR, str(tmp_path / "etc" / "klorb-config.json"))
    monkeypatch.setattr(process_config_module, "KLORB_CONFIG_DIR", tmp_path / "user-config")
    monkeypatch.setattr(process_config_module, "_default_config_layer", lambda: {})


def test_defaults_when_no_config_files_exist(tmp_path: Path) -> None:
    process_config = load_process_config(cwd=tmp_path)
    # workspace_root is always set from find_workspace_root(cwd), not SessionConfig's own
    # Path.cwd() default_factory — see test_workspace_root_falls_back_to_cwd_when_no_klorb_dir_found.
    assert process_config.session == SessionConfig(workspace=Workspace(path=tmp_path))
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
    assert process_config.compatibility_claude_markdown is False
    assert process_config.theme is None


def test_default_config_layer_reads_the_packaged_resource() -> None:
    layer = _real_default_config_layer()
    assert layer["sessionDefaults"]["model"] == DEFAULT_MODEL
    assert "~/.ssh" in layer["sessionDefaults"]["readDirs"]["deny"]


def test_default_config_layer_strips_the_schema_envelope() -> None:
    layer = _real_default_config_layer()
    assert "schema" not in layer


def test_default_config_layer_is_merged_when_no_other_config_files_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(process_config_module, "_default_config_layer", _real_default_config_layer)

    process_config = load_process_config(cwd=tmp_path)

    assert process_config.session.model == DEFAULT_MODEL
    assert Path("~/.ssh") in process_config.session.read_dirs.deny


def test_default_config_layer_is_overridden_by_etc_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(process_config_module, "_default_config_layer", _real_default_config_layer)
    _write_config(tmp_path / "etc" / "klorb-config.json", {"sessionDefaults": {"model": "etc/model"}})

    process_config = load_process_config(cwd=tmp_path)

    assert process_config.session.model == "etc/model"


def test_default_config_layer_readdirs_deny_survives_later_layers_own_allow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A project config that only adds an `allow` entry must not drop the packaged layer's
    own `deny` list — see docs/specs/permissions.md: lists concatenate, they never replace."""
    monkeypatch.setattr(process_config_module, "_default_config_layer", _real_default_config_layer)
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {"sessionDefaults": {"readDirs": {"allow": ["/ok"]}}})

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))

    assert Path("~/.ssh") in process_config.session.read_dirs.deny
    assert Path("/ok") in process_config.session.read_dirs.allow


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
            "ui.theme": "nord",
        },
    )

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
    assert process_config.prompt_input_max_lines == 20
    assert process_config.thinking_token_budgets == {"low": 1_000, "medium": 2_000, "high": 3_000}
    assert process_config.read_file_max_lines == 500
    assert process_config.edit_file_drift_search_radius == 5
    assert process_config.grep_max_results == 50
    assert process_config.find_file_max_results == 75
    assert process_config.openrouter_base_url == "https://gateway.example.com/v1"
    assert process_config.shell_command == "/bin/zsh"
    assert process_config.shell_timeout_seconds == 30
    assert process_config.theme == "nord"


def test_compatibility_claude_markdown_is_overridable_via_config_file(tmp_path: Path) -> None:
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {"compatibility.claudeMarkdown": True},
    )

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
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

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
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
        process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))

    assert process_config.session.interactive is True
    assert "interactive" in caplog.text


def test_unrecognized_config_key_is_dropped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"totally.madeUp": "value"})

    with caplog.at_level(logging.WARNING, logger="klorb.process_config"):
        process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))

    assert process_config.session.model == DEFAULT_MODEL
    assert "totally.madeUp" in caplog.text


def test_project_config_file_overrides_defaults(tmp_path: Path) -> None:
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"sessionDefaults": {"model": "project/model"}})

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
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

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
    assert process_config.session.model == "project/model"


def test_config_flag_path_overrides_project_config(tmp_path: Path) -> None:
    _write_config(tmp_path / ".klorb" / "klorb-config.json", {"sessionDefaults": {"model": "project/model"}})
    flag_path = tmp_path / "extra-config.json"
    _write_config(flag_path, {"sessionDefaults": {"model": "flag/model"}})

    process_config = load_process_config(
        config_flag_path=flag_path, cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
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

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
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

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
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

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
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

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
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

    process_config = load_process_config(  # must not raise
        cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
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
        process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))

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


def test_workspace_trust_has_no_on_disk_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """`workspace.trusted` must never be settable from klorb-config.json — a project must not
    be able to grant itself trust via its own config file. Written to the per-user layer
    (always loaded, unlike the trust-gated project layer — see
    docs/specs/projects-and-trust.md) so this test isn't entangled with `workspace` trust-gating
    at all: the key must have no effect at *any* layer, on top of the untrusted default
    `workspace` this test leaves untouched."""
    _write_config(tmp_path / "user-config" / "klorb-config.json", {"isWorkspaceTrusted": True})

    with caplog.at_level(logging.WARNING, logger="klorb.process_config"):
        process_config = load_process_config(cwd=tmp_path)

    assert process_config.session.workspace.trusted is False
    assert "isWorkspaceTrusted" in caplog.text


def test_key_maps_deliberately_exclude_workspace() -> None:
    """Deep-inspect SESSION_KEY_MAP/PROCESS_KEY_MAP themselves, not just
    load_process_config()'s observable behavior (test_workspace_trust_has_no_on_disk_key above
    already covers that): a static, structural check that catches the mistake at the source
    (the map definitions) rather than only downstream, and fails with a clear message pointing
    at *why* even if a future change accidentally makes the behavioral test pass some other way.

    See docs/specs/projects-and-trust.md: `SessionConfig.workspace` (and so `workspace.trusted`)
    is deliberately absent from both maps, at any key spelling — a project config file granting
    itself trust would defeat the entire point of gating ReadFile's hard workspace-root
    boundary on it. If this assertion ever fails, do not "fix" it by updating the test — the
    fix is almost certainly to remove whatever entry was just added to one of the maps.
    """
    assert "workspace" not in SESSION_KEY_MAP.values()
    assert "workspace" not in SESSION_KEY_MAP
    assert "workspace" not in PROCESS_KEY_MAP.values()
    assert "workspace" not in PROCESS_KEY_MAP
    assert "isWorkspaceTrusted" not in PROCESS_KEY_MAP
    assert "isWorkspaceTrusted" not in SESSION_KEY_MAP


def test_workspace_root_uses_find_workspace_root_result(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".klorb").mkdir(parents=True)
    nested = project / "src" / "sub"
    nested.mkdir(parents=True)

    process_config = load_process_config(cwd=nested)
    assert process_config.session.workspace.path == project


def test_workspace_root_falls_back_to_cwd_when_no_klorb_dir_found(tmp_path: Path) -> None:
    process_config = load_process_config(cwd=tmp_path)
    assert process_config.session.workspace.path == tmp_path


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

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))
    table = DirectoryAccessTable(
        process_config.session.write_dirs, process_config.session.workspace.path)
    assert table.evaluate(sensitive / "key.txt") == "deny"


def test_workspace_defaults_to_unregistered_untrusted_from_find_workspace_root(tmp_path: Path) -> None:
    """With no `workspace` argument, `load_process_config` synthesizes a conservative one:
    unregistered (`id=None`, `is_project=False`) and untrusted, rooted at
    `find_workspace_root(cwd)` — see docs/specs/projects-and-trust.md."""
    project = tmp_path / "project"
    (project / ".klorb").mkdir(parents=True)
    nested = project / "src"
    nested.mkdir()

    process_config = load_process_config(cwd=nested)

    assert process_config.session.workspace == Workspace(
        path=project, is_project=False, trusted=False)


def test_untrusted_workspace_skips_project_config_layer_entirely(tmp_path: Path) -> None:
    """An untrusted workspace's `.klorb/klorb-config.json` isn't merely ignored once read — it
    is never read at all, so it can't even reach the "unrecognized key" warning path. This is
    what actually protects a hostile, downloaded-and-unzipped repository's `readDirs.allow`
    from reaching the concatenated list at all (see docs/specs/projects-and-trust.md and
    docs/adrs/gate-read-hard-boundary-on-workspace-trust.md)."""
    _write_config(
        tmp_path / ".klorb" / "klorb-config.json",
        {"sessionDefaults": {"model": "hostile/model", "readDirs": {"allow": ["/whatever"]}}})

    process_config = load_process_config(
        cwd=tmp_path, workspace=Workspace(path=tmp_path, trusted=False))

    assert process_config.session.model == DEFAULT_MODEL
    assert Path("/whatever") not in process_config.session.read_dirs.allow


def test_load_process_config_sets_workspace_from_argument(tmp_path: Path) -> None:
    workspace = Workspace(id="some-uuid", path=tmp_path, is_project=True, trusted=True)

    process_config = load_process_config(cwd=tmp_path, workspace=workspace)

    assert process_config.session.workspace == workspace


def test_persist_theme_writes_ui_theme_key_to_user_config() -> None:
    persist_theme("nord")

    raw = json.loads(user_config_path().read_text(encoding="utf-8"))
    assert raw["ui.theme"] == "nord"
    assert raw["schema"] == {"name": "klorb-config", "version": "1.0.0"}


def test_persist_theme_preserves_other_keys_already_in_the_file(tmp_path: Path) -> None:
    _write_config(user_config_path(), {"sessionDefaults": {"model": "user/model"}})

    persist_theme("gruvbox")

    raw = json.loads(user_config_path().read_text(encoding="utf-8"))
    assert raw["ui.theme"] == "gruvbox"
    assert raw["sessionDefaults"]["model"] == "user/model"


def test_persist_theme_is_picked_up_by_a_later_load(tmp_path: Path) -> None:
    persist_theme("dracula")

    process_config = load_process_config(cwd=tmp_path)

    assert process_config.theme == "dracula"


def test_persist_session_default_creates_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "klorb-config.json"

    process_config_module.persist_session_default(path, "model", "other/model")

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["sessionDefaults"]["model"] == "other/model"
    assert written["schema"] == {"name": "klorb-config", "version": "1.0.0"}


def test_persist_session_default_preserves_other_keys(tmp_path: Path) -> None:
    path = tmp_path / "klorb-config.json"
    _write_config(path, {
        "sessionDefaults": {"model": "old/model", "thinking.effort": "high"},
        "shell.command": "/bin/zsh",
    })

    process_config_module.persist_session_default(path, "model", "new/model")

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["sessionDefaults"]["model"] == "new/model"
    assert written["sessionDefaults"]["thinking.effort"] == "high"
    assert written["shell.command"] == "/bin/zsh"


def test_persist_session_default_is_picked_up_by_a_later_load(tmp_path: Path) -> None:
    path = tmp_path / ".klorb" / "klorb-config.json"
    process_config_module.persist_session_default(path, "model", "persisted/model")

    process_config = load_process_config(cwd=tmp_path, workspace=_trusted_workspace(tmp_path))

    assert process_config.session.model == "persisted/model"
