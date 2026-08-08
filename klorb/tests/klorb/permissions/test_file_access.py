# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.file_access: FileRules/FileAccessTable exact-match and
`*`-wildcard evaluation. See docs/specs/permissions.md's "File access" section.
"""

from pathlib import Path

import pytest

from klorb.permissions.file_access import FileAccessTable, FileRules


def test_deny_beats_allow_regardless_of_specificity(tmp_path: Path) -> None:
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"

    table = FileAccessTable(FileRules(deny=[a], allow=[a, b]), tmp_path)

    assert table.evaluate(a) == "deny"
    assert table.evaluate(b) == "allow"


def test_ask_wins_over_allow_but_loses_to_deny(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"

    all_three = FileAccessTable(FileRules(deny=[target], ask=[target], allow=[target]), tmp_path)
    assert all_three.evaluate(target) == "deny"

    ask_and_allow = FileAccessTable(FileRules(ask=[target], allow=[target]), tmp_path)
    assert ask_and_allow.evaluate(target) == "ask"


def test_no_matching_rule_returns_none(tmp_path: Path) -> None:
    table = FileAccessTable(FileRules(allow=[tmp_path / "other.txt"]), tmp_path)
    assert table.evaluate(tmp_path / "f.txt") is None


def test_exact_match_only_no_containment(tmp_path: Path) -> None:
    """Unlike DirectoryAccessTable, a rule naming a directory does not match files inside it —
    FileAccessTable is exact-match only."""
    directory = tmp_path / "some_dir"
    directory.mkdir()

    table = FileAccessTable(FileRules(allow=[directory]), tmp_path)

    assert table.evaluate(directory) == "allow"
    assert table.evaluate(directory / "f.txt") is None


def test_device_path_outside_workspace_root_matches_exactly(tmp_path: Path) -> None:
    """The motivating case: a rule naming a path outside the workspace root (e.g. a character
    device) still matches exactly, since FileAccessTable has no workspace-root containment
    notion at all -- that boundary lives in klorb.permissions.workspace, not here."""
    table = FileAccessTable(FileRules(allow=[Path("/dev/null")]), tmp_path)
    assert table.evaluate(Path("/dev/null")) == "allow"
    assert table.evaluate(Path("/dev/zero")) is None


def test_symlinked_rule_file_is_canonicalized(tmp_path: Path) -> None:
    real_file = tmp_path / "real.txt"
    real_file.write_text("x")
    link = tmp_path / "link.txt"
    link.symlink_to(real_file)

    table = FileAccessTable(FileRules(deny=[link]), tmp_path)

    assert table.evaluate(real_file) == "deny"


def test_relative_rule_path_is_resolved_against_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    table = FileAccessTable(FileRules(allow=[Path("f.txt")]), workspace)

    assert table.evaluate(workspace / "f.txt") == "allow"
    assert table.evaluate(workspace / "other.txt") is None


def test_tilde_rule_path_expands_to_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    table = FileAccessTable(FileRules(deny=[Path("~/secret.txt")]), workspace)

    assert table.evaluate(home / "secret.txt") == "deny"


def test_wildcard_rule_matches_by_extension(tmp_path: Path) -> None:
    table = FileAccessTable(FileRules(deny=[Path("*.pem")]), tmp_path)

    assert table.evaluate(tmp_path / "key.pem") == "deny"
    assert table.evaluate(tmp_path / "key.crt") is None


def test_wildcard_rule_star_spans_directory_separators(tmp_path: Path) -> None:
    """A single `*` matches `/` too, so a rule with no literal directory prefix reaches every
    depth under the workspace root, not just direct children."""
    table = FileAccessTable(FileRules(deny=[Path("*.pem")]), tmp_path)

    assert table.evaluate(tmp_path / "sub" / "dir" / "key.pem") == "deny"


def test_wildcard_rule_with_directory_prefix_scopes_the_match(tmp_path: Path) -> None:
    table = FileAccessTable(FileRules(deny=[Path("secrets/*.pem")]), tmp_path)

    assert table.evaluate(tmp_path / "secrets" / "key.pem") == "deny"
    assert table.evaluate(tmp_path / "other" / "key.pem") is None


def test_wildcard_rule_other_glob_characters_are_literal(tmp_path: Path) -> None:
    """`?` and `[...]` carry no special meaning in this grammar -- only `*` does."""
    table = FileAccessTable(FileRules(allow=[Path("file[1].txt")]), tmp_path)

    assert table.evaluate(tmp_path / "file[1].txt") == "allow"
    assert table.evaluate(tmp_path / "file1.txt") is None
    assert table.evaluate(tmp_path / "fileX.txt") is None
