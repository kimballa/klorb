# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.widgets.files_panel."""

from klorb.session import FileAccessMode
from klorb.tui.widgets.files_panel import FileActivityRowData, FilesPanel


def _row(
    abs_path: str = "/ws/a.txt", rel_path: str = "a.txt", mode: FileAccessMode = "read",
) -> FileActivityRowData:
    return FileActivityRowData(abs_path=abs_path, rel_path=rel_path, mode=mode)


def test_render_row_label_read_entry_is_marked_r() -> None:
    label = FilesPanel._render_row_label(_row(mode="read"))

    assert str(label) == "R a.txt"


def test_render_row_label_write_entry_is_marked_m() -> None:
    label = FilesPanel._render_row_label(_row(mode="write"))

    assert str(label) == "M a.txt"


def test_render_row_label_truncates_a_long_nested_path() -> None:
    long_path = "some/very/deeply/nested/directory/structure/that/is/quite/long/file.txt"
    label = FilesPanel._render_row_label(_row(rel_path=long_path))

    text = str(label)
    assert text.startswith("R ")
    assert text.endswith("/file.txt")
    assert ".." in text
    # The filename itself is never truncated.
    assert "file.txt" in text


def test_render_footer_empty() -> None:
    assert FilesPanel._render_footer([]) == "No files accessed yet."


def test_render_footer_counts_files_and_writes() -> None:
    rows = [_row(mode="read"), _row(mode="write"), _row(mode="write")]

    assert FilesPanel._render_footer(rows) == "3 files, 2 written"


def test_render_footer_singular_file() -> None:
    assert FilesPanel._render_footer([_row(mode="read")]) == "1 file, 0 written"
