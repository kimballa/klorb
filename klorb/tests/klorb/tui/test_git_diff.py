# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.git_diff.git_diff_hunks_for."""

import subprocess
from pathlib import Path

from klorb.tui.git_diff import git_diff_hunks_for


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")


def test_returns_none_outside_a_git_repository(tmp_path: Path) -> None:
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    file_path = outside / "a.txt"
    file_path.write_text("hello\n")

    assert git_diff_hunks_for(outside, file_path) is None


def test_diffs_a_modified_tracked_file_against_head(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    file_path = repo / "a.txt"
    file_path.write_text("one\ntwo\nthree\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "initial")

    file_path.write_text("one\nTWO\nthree\n")
    hunks = git_diff_hunks_for(repo, file_path)

    assert hunks is not None
    kinds = [line.kind for hunk in hunks for line in hunk.lines]
    texts = [line.text for hunk in hunks for line in hunk.lines]
    assert "del" in kinds
    assert "add" in kinds
    assert "two" in texts
    assert "TWO" in texts


def test_unmodified_tracked_file_has_no_hunks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    file_path = repo / "a.txt"
    file_path.write_text("one\ntwo\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "initial")

    hunks = git_diff_hunks_for(repo, file_path)

    assert hunks == []


def test_a_file_git_does_not_know_about_diffs_as_all_additions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "committed.txt").write_text("x\n")
    _git(repo, "add", "committed.txt")
    _git(repo, "commit", "-q", "-m", "initial")

    new_file = repo / "new.txt"
    new_file.write_text("brand new\ncontent\n")
    hunks = git_diff_hunks_for(repo, new_file)

    assert hunks is not None
    lines = [line for hunk in hunks for line in hunk.lines]
    assert all(line.kind == "add" for line in lines)
    assert [line.text for line in lines] == ["brand new", "content"]


def test_a_file_under_a_subdirectory_of_the_repo_resolves_correctly(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    nested = repo / "src" / "nested.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("one\n")
    _git(repo, "add", "src/nested.txt")
    _git(repo, "commit", "-q", "-m", "initial")

    nested.write_text("ONE\n")
    hunks = git_diff_hunks_for(repo, nested)

    assert hunks is not None
    texts = [line.text for hunk in hunks for line in hunk.lines]
    assert "one" in texts
    assert "ONE" in texts
