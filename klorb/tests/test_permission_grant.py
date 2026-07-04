# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.grant: computing and persisting Allow grants for the
interactive "ask" confirmation flow. See docs/specs/permissions.md's "Interactive ask
confirmation" section.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from klorb import process_config as process_config_module
from klorb.permissions.directory_access import DirRules
from klorb.permissions.grant import (
    _promote_table,
    apply_permission_grant,
    compute_grant_paths,
)
from klorb.process_config import (
    CONFIG_SCHEMA_NAME,
    SESSION_DEFAULTS_KEY,
    ProcessConfig,
    project_config_path,
    user_config_path,
)
from klorb.schema_envelope import read_versioned_json
from klorb.session import SessionConfig


@pytest.fixture(autouse=True)
def _isolate_homedir_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `user_config_path()` at an empty location under `tmp_path`, so tests never touch
    the developer's own `~/.config/klorb/klorb-config.json`."""
    monkeypatch.setattr(process_config_module, "KLORB_CONFIG_DIR", tmp_path / "homedir")


def _write_config(path: Path, session_defaults: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema": {"name": "klorb-config", "version": "1.0.0"},
        SESSION_DEFAULTS_KEY: session_defaults,
    }
    path.write_text(json.dumps(envelope), encoding="utf-8")


def _read_session_defaults(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = read_versioned_json(
        path, expected_schema_name=CONFIG_SCHEMA_NAME).get(SESSION_DEFAULTS_KEY, {})
    return result


# --- compute_grant_paths ---


def test_compute_grant_paths_falls_back_to_containing_directory_when_nothing_matched(
    tmp_path: Path,
) -> None:
    candidate = tmp_path / "sub" / "f.txt"
    assert compute_grant_paths(DirRules(), DirRules(), tmp_path, candidate, is_write=False) == [
        candidate.parent]
    assert compute_grant_paths(DirRules(), DirRules(), tmp_path, candidate, is_write=True) == [
        candidate.parent]


def test_compute_grant_paths_promotes_matched_ask_rules_own_path(tmp_path: Path) -> None:
    ask_dir = tmp_path / "maybe"
    candidate = ask_dir / "sub" / "f.txt"
    read_dirs = DirRules(ask=[ask_dir])

    assert compute_grant_paths(read_dirs, DirRules(), tmp_path, candidate, is_write=False) == [ask_dir]


def test_compute_grant_paths_read_only_ignores_write_dirs_entirely(tmp_path: Path) -> None:
    """A broader writeDirs.ask match must never leak into a read-only access's grant."""
    write_dirs = DirRules(ask=[tmp_path])
    candidate = tmp_path / "sub" / "f.txt"

    assert compute_grant_paths(DirRules(), write_dirs, tmp_path, candidate, is_write=False) == [
        candidate.parent]


def test_compute_grant_paths_write_access_unions_both_tables(tmp_path: Path) -> None:
    """read_dirs and write_dirs can each have their own, differently-specific ask rule
    matching the same candidate; both must be promoted, not just one."""
    read_ask_dir = tmp_path
    write_ask_dir = tmp_path / "sub"
    candidate = write_ask_dir / "f.txt"
    read_dirs = DirRules(ask=[read_ask_dir])
    write_dirs = DirRules(ask=[write_ask_dir])

    granted = compute_grant_paths(read_dirs, write_dirs, tmp_path, candidate, is_write=True)
    assert set(granted) == {read_ask_dir, write_ask_dir}


def test_compute_grant_paths_multiple_overlapping_ask_rules_all_returned(tmp_path: Path) -> None:
    broad = tmp_path / "broad"
    narrow = tmp_path / "broad" / "narrow"
    candidate = narrow / "f.txt"
    read_dirs = DirRules(ask=[broad, narrow])

    granted = compute_grant_paths(read_dirs, DirRules(), tmp_path, candidate, is_write=False)
    assert set(granted) == {broad, narrow}


# --- _promote_table ---


def test_promote_table_never_mutates_input_in_place(tmp_path: Path) -> None:
    original_ask = [tmp_path]
    original_allow: list[Path] = []
    rules = DirRules(ask=list(original_ask), allow=list(original_allow))

    _promote_table(rules, tmp_path, [tmp_path])

    assert rules.ask == original_ask
    assert rules.allow == original_allow


# --- apply_permission_grant: "session" scope ---


def test_session_scope_mutates_only_in_memory_session_config(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace_root=tmp_path)
    process_config = ProcessConfig()
    target = tmp_path / "sub" / "f.txt"

    apply_permission_grant("session", session_config, process_config, target, is_write=True)

    assert session_config.read_dirs.allow == [target.parent]
    assert session_config.write_dirs.allow == [target.parent]
    assert process_config.session.read_dirs == DirRules()
    assert process_config.session.write_dirs == DirRules()
    assert not project_config_path(tmp_path).exists()
    assert not user_config_path().exists()


def test_session_scope_read_only_never_touches_write_dirs(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace_root=tmp_path)
    process_config = ProcessConfig()
    target = tmp_path / "sub" / "f.txt"

    apply_permission_grant("session", session_config, process_config, target, is_write=False)

    assert session_config.read_dirs.allow == [target.parent]
    assert session_config.write_dirs == DirRules()


# --- apply_permission_grant: "workspace" scope ---


def test_workspace_scope_mutates_session_and_process_template(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace_root=tmp_path)
    process_config = ProcessConfig()
    target = tmp_path / "sub" / "f.txt"

    apply_permission_grant("workspace", session_config, process_config, target, is_write=True)

    assert session_config.read_dirs.allow == [target.parent]
    assert session_config.write_dirs.allow == [target.parent]
    assert process_config.session.read_dirs.allow == [target.parent]
    assert process_config.session.write_dirs.allow == [target.parent]


def test_workspace_scope_with_no_process_config_skips_the_ripple_but_still_persists(
    tmp_path: Path,
) -> None:
    """A caller with no live `ProcessConfig` (e.g. a `Session` constructed without one) still
    gets a working grant: the live `SessionConfig` and the on-disk file, neither of which needs
    the `ProcessConfig` object itself, are still updated -- only the `process_config.session.*`
    ripple is skipped, since there's nothing to ripple into."""
    session_config = SessionConfig(workspace_root=tmp_path)
    target = tmp_path / "sub" / "f.txt"

    apply_permission_grant("workspace", session_config, None, target, is_write=True)

    assert session_config.read_dirs.allow == [target.parent]
    assert session_config.write_dirs.allow == [target.parent]
    session_defaults = _read_session_defaults(project_config_path(tmp_path))
    assert session_defaults["readDirs"]["allow"] == [str(target.parent)]


def test_workspace_scope_creates_file_if_missing(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace_root=tmp_path)
    process_config = ProcessConfig()
    target = tmp_path / "sub" / "f.txt"
    path = project_config_path(tmp_path)
    assert not path.exists()

    apply_permission_grant("workspace", session_config, process_config, target, is_write=True)

    assert path.is_file()
    session_defaults = _read_session_defaults(path)
    assert session_defaults["readDirs"]["allow"] == [str(target.parent)]
    assert session_defaults["writeDirs"]["allow"] == [str(target.parent)]


def test_workspace_scope_preserves_unrelated_keys_in_existing_file(tmp_path: Path) -> None:
    path = project_config_path(tmp_path)
    _write_config(path, {
        "model": "project/model",
        "readDirs": {"deny": ["/nope"], "ask": [], "allow": ["/already-allowed"]},
        "writeDirs": {"deny": [], "ask": [], "allow": []},
    })
    session_config = SessionConfig(workspace_root=tmp_path)
    process_config = ProcessConfig()
    target = tmp_path / "sub" / "f.txt"

    apply_permission_grant("workspace", session_config, process_config, target, is_write=True)

    session_defaults = _read_session_defaults(path)
    assert session_defaults["model"] == "project/model"
    assert session_defaults["readDirs"]["deny"] == ["/nope"]
    assert set(session_defaults["readDirs"]["allow"]) == {"/already-allowed", str(target.parent)}


def test_workspace_scope_promotes_matched_ask_rule_and_removes_it_from_the_same_file(
    tmp_path: Path,
) -> None:
    ask_dir = tmp_path / "maybe"
    path = project_config_path(tmp_path)
    _write_config(path, {
        "readDirs": {"deny": [], "ask": [str(ask_dir)], "allow": []},
        "writeDirs": {"deny": [], "ask": [str(ask_dir)], "allow": []},
    })
    session_config = SessionConfig(
        workspace_root=tmp_path, read_dirs=DirRules(ask=[ask_dir]), write_dirs=DirRules(ask=[ask_dir]))
    process_config = ProcessConfig()
    target = ask_dir / "sub" / "f.txt"

    apply_permission_grant("workspace", session_config, process_config, target, is_write=True)

    assert session_config.read_dirs.ask == []
    assert session_config.read_dirs.allow == [ask_dir]
    session_defaults = _read_session_defaults(path)
    assert session_defaults["readDirs"]["ask"] == []
    assert session_defaults["readDirs"]["allow"] == [str(ask_dir)]
    assert session_defaults["writeDirs"]["ask"] == []
    assert session_defaults["writeDirs"]["allow"] == [str(ask_dir)]


def test_workspace_scope_never_opens_the_homedir_file(tmp_path: Path) -> None:
    """Safety-critical negative test: a workspace-scope grant must never touch the homedir (or
    /etc) file, even to clean up a matching, now-redundant ask entry there -- that direction is
    reserved for a homedir-scope grant only (see the next section)."""
    homedir_path = user_config_path()
    _write_config(homedir_path, {"readDirs": {"deny": [], "ask": [str(tmp_path)], "allow": []}})
    homedir_mtime_before = homedir_path.stat().st_mtime_ns

    session_config = SessionConfig(workspace_root=tmp_path, read_dirs=DirRules(ask=[tmp_path]))
    process_config = ProcessConfig()
    target = tmp_path / "sub" / "f.txt"

    apply_permission_grant("workspace", session_config, process_config, target, is_write=False)

    assert homedir_path.stat().st_mtime_ns == homedir_mtime_before
    session_defaults = _read_session_defaults(homedir_path)
    assert session_defaults["readDirs"]["ask"] == [str(tmp_path)]


# --- apply_permission_grant: "homedir" scope ---


def test_homedir_scope_creates_file_if_missing(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace_root=tmp_path)
    process_config = ProcessConfig()
    target = tmp_path / "sub" / "f.txt"
    path = user_config_path()
    assert not path.exists()

    apply_permission_grant("homedir", session_config, process_config, target, is_write=True)

    assert path.is_file()
    session_defaults = _read_session_defaults(path)
    assert session_defaults["readDirs"]["allow"] == [str(target.parent)]
    assert session_defaults["writeDirs"]["allow"] == [str(target.parent)]


def test_homedir_scope_with_no_process_config_skips_the_ripple_but_still_persists(
    tmp_path: Path,
) -> None:
    session_config = SessionConfig(workspace_root=tmp_path)
    target = tmp_path / "sub" / "f.txt"
    path = user_config_path()

    apply_permission_grant("homedir", session_config, None, target, is_write=True)

    assert path.is_file()
    assert session_config.read_dirs.allow == [target.parent]
    session_defaults = _read_session_defaults(path)
    assert session_defaults["readDirs"]["allow"] == [str(target.parent)]


def test_homedir_scope_cleans_matching_ask_entry_out_of_workspace_file(tmp_path: Path) -> None:
    ask_dir = tmp_path / "maybe"
    workspace_path = project_config_path(tmp_path)
    _write_config(workspace_path, {
        "readDirs": {"deny": [], "ask": [str(ask_dir)], "allow": ["/keep-me"]},
        "writeDirs": {"deny": [], "ask": [], "allow": []},
    })
    session_config = SessionConfig(workspace_root=tmp_path, read_dirs=DirRules(ask=[ask_dir]))
    process_config = ProcessConfig()
    target = ask_dir / "sub" / "f.txt"

    apply_permission_grant("homedir", session_config, process_config, target, is_write=False)

    # Removal only: the workspace file's ask entry is gone, but nothing was added to its allow.
    workspace_defaults = _read_session_defaults(workspace_path)
    assert workspace_defaults["readDirs"]["ask"] == []
    assert workspace_defaults["readDirs"]["allow"] == ["/keep-me"]

    homedir_defaults = _read_session_defaults(user_config_path())
    assert homedir_defaults["readDirs"]["allow"] == [str(ask_dir)]


def test_homedir_scope_workspace_cleanup_is_a_noop_when_workspace_file_is_absent(
    tmp_path: Path,
) -> None:
    session_config = SessionConfig(workspace_root=tmp_path)
    process_config = ProcessConfig()
    target = tmp_path / "sub" / "f.txt"

    apply_permission_grant("homedir", session_config, process_config, target, is_write=True)  # must not raise

    assert not project_config_path(tmp_path).exists()


# --- write access grants read_dirs too; read-only never touches write_dirs ---


def test_write_access_grants_read_dirs_even_when_only_write_dirs_had_the_ask_rule(
    tmp_path: Path,
) -> None:
    ask_dir = tmp_path / "maybe"
    session_config = SessionConfig(workspace_root=tmp_path, write_dirs=DirRules(ask=[ask_dir]))
    process_config = ProcessConfig()
    target = ask_dir / "f.txt"

    apply_permission_grant("session", session_config, process_config, target, is_write=True)

    assert session_config.read_dirs.allow == [ask_dir]
    assert session_config.write_dirs.allow == [ask_dir]


def test_read_only_access_never_mutates_write_dirs(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace_root=tmp_path, write_dirs=DirRules(ask=[tmp_path]))
    process_config = ProcessConfig()
    target = tmp_path / "f.txt"

    apply_permission_grant("session", session_config, process_config, target, is_write=False)

    assert session_config.write_dirs == DirRules(ask=[tmp_path])  # untouched


# --- idempotency ---


def test_repeated_grant_does_not_duplicate_allow_entries(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace_root=tmp_path)
    process_config = ProcessConfig()
    target = tmp_path / "sub" / "f.txt"

    apply_permission_grant("workspace", session_config, process_config, target, is_write=True)
    apply_permission_grant("workspace", session_config, process_config, target, is_write=True)

    assert session_config.read_dirs.allow == [target.parent]
    assert session_config.write_dirs.allow == [target.parent]
    session_defaults = _read_session_defaults(project_config_path(tmp_path))
    assert session_defaults["readDirs"]["allow"] == [str(target.parent)]
    assert session_defaults["writeDirs"]["allow"] == [str(target.parent)]
