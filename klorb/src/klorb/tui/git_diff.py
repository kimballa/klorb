# © Copyright 2026 Aaron Kimball
"""Regenerates a file's diff against its git baseline for the Files panel, computed fresh each
time it's requested."""

import subprocess
from pathlib import Path

from klorb.tools.util.diff_lines import DiffHunk, build_diff_hunks

_GIT_TIMEOUT_SECONDS = 5.0


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_repo_root(start_dir: Path) -> Path | None:
    """The top-level directory of the git working tree containing `start_dir`, or `None` if it
    isn't inside one (or `git` isn't available)."""
    result = _run_git(start_dir, "rev-parse", "--show-toplevel")
    if result is None or result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def _git_show_head(repo_root: Path, rel_path: Path) -> str | None:
    """`rel_path`'s content at `HEAD`, or `None` if it doesn't exist there (a new, not-yet-
    committed file)."""
    result = _run_git(repo_root, "show", f"HEAD:{rel_path.as_posix()}")
    if result is None or result.returncode != 0:
        return None
    return result.stdout


def git_diff_hunks_for(workspace_root: Path, abs_path: Path) -> list[DiffHunk] | None:
    """Recompute `abs_path`'s diff hunks by diffing its `git show HEAD:<path>` content (empty if
    the file doesn't exist at `HEAD`) against its current on-disk content, read fresh on every
    call. Returns `None` if `workspace_root` isn't inside a git working tree."""
    repo_root = _git_repo_root(workspace_root)
    if repo_root is None:
        return None
    try:
        rel_path = abs_path.resolve().relative_to(repo_root)
    except ValueError:
        return None
    old_text = _git_show_head(repo_root, rel_path)
    try:
        new_text: str | None = abs_path.read_text(encoding="utf-8")
    except OSError:
        new_text = None
    old_lines = old_text.splitlines() if old_text is not None else []
    new_lines = new_text.splitlines() if new_text is not None else []
    return build_diff_hunks(old_lines, new_lines)
