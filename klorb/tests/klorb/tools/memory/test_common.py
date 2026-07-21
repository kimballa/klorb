# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.memory.common."""

from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.memory import common as memory_common_module
from klorb.tools.memory.common import (
    memory_namespace_dir,
    read_memory_topic,
    require_workspace_namespace_accessible,
    validate_first_line_not_blank,
    validate_memory_filename,
    workspace_namespace_accessible,
)
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(workspace_root: Path, *, trusted: bool = False) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(workspace=Workspace(path=workspace_root, trusted=trusted)))


def test_global_namespace_resolves_under_klorb_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(memory_common_module, "KLORB_DATA_DIR", data_dir)

    assert memory_namespace_dir(_context(tmp_path), "global") == data_dir / "memories"


def test_workspace_namespace_resolves_under_dot_klorb(tmp_path: Path) -> None:
    assert (
        memory_namespace_dir(_context(tmp_path), "workspace")
        == tmp_path / ".klorb" / "memories")


def test_namespace_directories_are_not_created_eagerly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(memory_common_module, "KLORB_DATA_DIR", tmp_path / "data")

    memory_namespace_dir(_context(tmp_path), "global")
    memory_namespace_dir(_context(tmp_path), "workspace")

    assert not (tmp_path / "data" / "memories").exists()
    assert not (tmp_path / ".klorb" / "memories").exists()


@pytest.mark.parametrize("filename", ["foo/bar.md", "/etc/passwd.md", "a\\b.md"])
def test_filename_with_separator_is_rejected(tmp_path: Path, filename: str) -> None:
    with pytest.raises(ValueError, match="path separator"):
        validate_memory_filename(filename, tmp_path / "memories")


@pytest.mark.parametrize("filename", ["../etc/passwd", "foo/../../bar.md"])
def test_filename_escape_attempts_are_rejected(tmp_path: Path, filename: str) -> None:
    """Both of these contain "/", so they're rejected by the separator check before the
    resolve-based escape check below ever runs -- see test_bare_dotdot_without_a_separator_is_
    rejected for a case that reaches the escape check itself."""
    with pytest.raises(ValueError, match="path separator"):
        validate_memory_filename(filename, tmp_path / "memories")


def test_bare_dotdot_without_a_separator_is_rejected(tmp_path: Path) -> None:
    """".." has no path separator, so it reaches the resolve-based escape check (rather than
    the separator check) -- but it also doesn't end in ".md", so the extension check rejects it
    first; this pins down that the extension check doesn't accidentally let it through."""
    with pytest.raises(ValueError, match="must end in"):
        validate_memory_filename("..", tmp_path / "memories")


def test_bare_leading_tilde_does_not_escape_the_namespace(tmp_path: Path) -> None:
    """"~" only expands via Path.expanduser() as a whole leading path component; since
    filenames may not contain "/", a filename can never consist of just "~" followed by a
    separator, so this never reaches the home directory."""
    namespace_dir = tmp_path / "memories"

    with pytest.raises(ValueError, match="must end in"):
        validate_memory_filename("~", namespace_dir)


def test_filename_not_ending_in_md_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must end in"):
        validate_memory_filename("notes", tmp_path / "memories")


def test_valid_filename_resolves_within_the_namespace_dir(tmp_path: Path) -> None:
    namespace_dir = tmp_path / "memories"

    resolved = validate_memory_filename("notes.md", namespace_dir)

    assert resolved == namespace_dir / "notes.md"


def test_same_filename_in_different_namespaces_are_independent(tmp_path: Path) -> None:
    global_dir = tmp_path / "global-memories"
    workspace_dir = tmp_path / "workspace-memories"
    global_dir.mkdir()
    workspace_dir.mkdir()

    global_path = validate_memory_filename("notes.md", global_dir)
    workspace_path = validate_memory_filename("notes.md", workspace_dir)
    global_path.write_text("Global notes\n")
    workspace_path.write_text("Workspace notes\n")

    workspace_path.unlink()

    assert global_path.read_text() == "Global notes\n"
    assert not workspace_path.exists()


def test_read_memory_topic_returns_first_line(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("My topic\nmore detail\n")

    assert read_memory_topic(path) == "My topic"


def test_read_memory_topic_is_empty_for_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("")

    assert read_memory_topic(path) == ""


def test_read_memory_topic_is_empty_for_blank_first_line(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("   \nsecond line\n")

    assert read_memory_topic(path) == ""


def test_validate_first_line_not_blank_accepts_a_real_topic() -> None:
    validate_first_line_not_blank("Topic\nbody\n", subject="test memory")  # must not raise


def test_validate_first_line_not_blank_rejects_empty_content() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        validate_first_line_not_blank("", subject="test memory")


def test_validate_first_line_not_blank_rejects_whitespace_only_first_line() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        validate_first_line_not_blank("   \nbody\n", subject="test memory")


def test_workspace_namespace_accessible_reflects_workspace_trust(tmp_path: Path) -> None:
    assert workspace_namespace_accessible(_context(tmp_path, trusted=False)) is False
    assert workspace_namespace_accessible(_context(tmp_path, trusted=True)) is True


def test_require_workspace_namespace_accessible_raises_when_untrusted(tmp_path: Path) -> None:
    with pytest.raises(PermissionError):
        require_workspace_namespace_accessible(_context(tmp_path, trusted=False), "workspace")


def test_require_workspace_namespace_accessible_allows_when_trusted(tmp_path: Path) -> None:
    require_workspace_namespace_accessible(_context(tmp_path, trusted=True), "workspace")  # no raise


def test_require_workspace_namespace_accessible_ignores_global_namespace(tmp_path: Path) -> None:
    require_workspace_namespace_accessible(_context(tmp_path, trusted=False), "global")  # no raise
