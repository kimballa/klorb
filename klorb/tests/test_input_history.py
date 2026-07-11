# © Copyright 2026 Aaron Kimball
"""Tests for klorb.workspace.input_history."""

from pathlib import Path

import pytest

from klorb.workspace import Workspace
from klorb.workspace import input_history as input_history_module
from klorb.workspace.input_history import (
    _escape_entry,
    _unescape_entry,
    append_history,
    load_history,
    project_history_dir,
    project_history_path,
)


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `input_history` at an empty `$KLORB_DATA_DIR` under `tmp_path`, so no test in
    this module reads or writes the developer's own `~/.local/share/klorb/projects/`."""
    monkeypatch.setattr(input_history_module, "KLORB_DATA_DIR", tmp_path / "data")


# --- escape / unescape round-trip ---


def test_escape_replaces_newlines_and_backslashes() -> None:
    assert _escape_entry("hello\nworld") == "hello\\nworld"
    assert _escape_entry("a\rb") == "a\\rb"
    assert _escape_entry("back\\slash") == "back\\\\slash"
    assert _escape_entry("plain") == "plain"
    assert _escape_entry("") == ""


def test_unescape_reverses_escape() -> None:
    assert _unescape_entry("hello\\nworld") == "hello\nworld"
    assert _unescape_entry("a\\rb") == "a\rb"
    assert _unescape_entry("back\\\\slash") == "back\\slash"


@pytest.mark.parametrize("entry", [
    "simple",
    "multi\nline\nentry",
    "carriage\rreturn",
    "back\\slash and newline\n",
    "mixed\\n literal and real\n",
    "",
    "\n",
    "\\n",
    "line1\nline2\rline3\\back",
])
def test_escape_unescape_round_trips(entry: str) -> None:
    assert _unescape_entry(_escape_entry(entry)) == entry


def test_unescape_keeps_unrecognized_escape_verbatim() -> None:
    # A lone backslash before a non-escape char is kept as-is, not silently dropped.
    assert _unescape_entry("a\\zb") == "a\\zb"
    assert _unescape_entry("trailing\\") == "trailing\\"


# --- project_history_dir / project_history_path ---


def test_project_history_dir_uses_registered_uuid_and_basename(tmp_path: Path) -> None:
    workspace = Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)
    assert project_history_dir(workspace) == (
        input_history_module.KLORB_DATA_DIR / "projects" / "abcd-1234-foobar")


def test_project_history_path_appends_history_filename(tmp_path: Path) -> None:
    workspace = Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)
    assert project_history_path(workspace) == (
        input_history_module.KLORB_DATA_DIR / "projects" / "abcd-1234-foobar" / "history")


def test_project_history_dir_uses_stable_hash_for_unregistered_workspace(tmp_path: Path) -> None:
    """Two unregistered workspaces at the same canonical path converge on one dir; a
    different path maps to a different dir (so distinct unregistered folders don't collide)."""
    path = tmp_path / "unreg-project"
    workspace = Workspace(path=path, is_project=False, trusted=False)
    dir_a = project_history_dir(workspace)
    dir_b = project_history_dir(Workspace(path=path, is_project=False, trusted=False))
    assert dir_a == dir_b
    assert dir_a.name.endswith("-unreg-project")

    other = project_history_dir(Workspace(path=tmp_path / "other-project"))
    assert other != dir_a


# --- load / append ---


def test_load_history_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert load_history(tmp_path / "does-not-exist") == []


def test_append_then_load_round_trips_single_entry(tmp_path: Path) -> None:
    path = tmp_path / "history"
    append_history(path, "hello world")
    assert load_history(path) == ["hello world"]


def test_append_multiple_entries_preserves_order(tmp_path: Path) -> None:
    path = tmp_path / "history"
    append_history(path, "first")
    append_history(path, "second")
    append_history(path, "third")
    assert load_history(path) == ["first", "second", "third"]


def test_append_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deep" / "history"
    append_history(path, "entry")
    assert path.is_file()
    assert load_history(path) == ["entry"]


def test_multi_line_entry_round_trips_through_file(tmp_path: Path) -> None:
    path = tmp_path / "history"
    entry = "line one\nline two\nline three"
    append_history(path, entry)
    assert load_history(path) == [entry]


def test_entries_with_special_characters_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "history"
    entries = ["a\\b", "c\nd", "e\rf", "plain", "\\n literal"]
    for entry in entries:
        append_history(path, entry)
    assert load_history(path) == entries


def test_load_history_drops_trailing_newline_not_empty_entry(tmp_path: Path) -> None:
    """A trailing newline is the record separator after the last entry, not a blank final
    entry — so the list never grows a spurious empty tail."""
    path = tmp_path / "history"
    append_history(path, "only")
    assert load_history(path) == ["only"]


def test_append_only_never_rewrites_existing_content(tmp_path: Path) -> None:
    """The key concurrency guarantee: appending never truncates or rewrites what's already
    there, so a second writer (another klorb instance in the same folder) appending after a
    first leaves the first's entry intact."""
    path = tmp_path / "history"
    append_history(path, "instance-one")
    append_history(path, "instance-two")
    text = path.read_text(encoding="utf-8")
    assert "instance-one" in text
    assert "instance-two" in text
    assert load_history(path) == ["instance-one", "instance-two"]
