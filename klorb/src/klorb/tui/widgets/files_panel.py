# © Copyright 2026 Aaron Kimball
"""`FilesPanel`: a docked, togglable right-hand panel listing every file read or written this
session, across the root session and every subagent beneath it. See docs/specs/terminal-repl.md.
"""

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from klorb.session import FileAccessMode
from klorb.tui.constants import SIDEBAR_WIDTH
from klorb.tui.widgets.file_finder import split_finder_row

FILES_LIST_ID = "files-panel-list"
_HEADER_ID = "files-panel-header"
_FOOTER_ID = "files-panel-footer"

_HEADER_TEXT = "Files"
_WRITE_MARKER = "M"
_READ_MARKER = "R"
_ROW_RESERVED_WIDTH = 4
"""Marker character plus its trailing space, plus `OptionList`'s default 1-column padding on
each side, reserved out of `SIDEBAR_WIDTH` for the path's own width budget."""


@dataclass(frozen=True)
class FileActivityRowData:
    """One row `FilesPanel.show_rows` renders: `abs_path` for reopening the file, `rel_path`
    (workspace-relative, posix-style) for display, and `mode` for the indicator column."""

    abs_path: str
    rel_path: str
    mode: FileAccessMode


class FilesPanelOption(Option):
    """An `OptionList` row carrying the `FileActivityRowData` it represents, so selecting it can
    recover which file to reopen."""

    def __init__(self, row: FileActivityRowData, label: Content, *, index: int) -> None:
        super().__init__(label, id=f"file-{index}")
        self.row = row


class FilesPanel(Vertical, can_focus=False):
    """Lists every file recorded by the process-wide `FileActivityTracker`, docked to the right
    edge of the screen and hidden until `Ctrl+F` (`FilesPanelMixin.action_toggle_files_panel`)
    first shows it. Selecting a row (Enter or click) reopens that file's current full content,
    diff-annotated against its git baseline when one is available for a written entry.
    """

    DEFAULT_CSS = f"""
    FilesPanel {{
        dock: right;
        width: {SIDEBAR_WIDTH};
        border-left: solid $accent;
        display: none;
    }}
    #{_HEADER_ID} {{
        background: $panel;
        color: $foreground;
        width: 1fr;
        padding: 0 1;
    }}
    #{FILES_LIST_ID} {{
        height: 1fr;
        width: 1fr;
        border: none;
        background: transparent;
    }}
    #{FILES_LIST_ID}:focus {{
        border: none;
    }}
    #{_FOOTER_ID} {{
        background: $panel;
        color: $text-muted;
        width: 1fr;
        padding: 0 1;
    }}
    """

    def compose(self) -> ComposeResult:
        yield Static(_HEADER_TEXT, id=_HEADER_ID, markup=False)
        yield OptionList(id=FILES_LIST_ID)
        yield Static("", id=_FOOTER_ID, markup=False)

    def show_rows(self, rows: list[FileActivityRowData]) -> None:
        """Replace the displayed rows with `rows`, and update the footer to a file/write-count
        summary."""
        option_list = self.query_one(f"#{FILES_LIST_ID}", OptionList)
        option_list.clear_options()
        for index, row in enumerate(rows):
            option_list.add_option(FilesPanelOption(row, self._render_row_label(row), index=index))
        footer = self.query_one(f"#{_FOOTER_ID}", Static)
        footer.update(self._render_footer(rows))

    @staticmethod
    def _render_row_label(row: FileActivityRowData) -> Content:
        marker = _WRITE_MARKER if row.mode == "write" else _READ_MARKER
        marker_style = "bold green" if row.mode == "write" else "dim"
        available_width = SIDEBAR_WIDTH - _ROW_RESERVED_WIDTH
        dir_part, file_part = split_finder_row(row.rel_path, available_width)
        if not dir_part:
            return Content.assemble((f"{marker} ", marker_style), file_part)
        return Content.assemble((f"{marker} ", marker_style), (dir_part, "$foreground-muted"), file_part)

    @staticmethod
    def _render_footer(rows: list[FileActivityRowData]) -> str:
        if not rows:
            return "No files accessed yet."
        written = sum(1 for row in rows if row.mode == "write")
        noun = "file" if len(rows) == 1 else "files"
        return f"{len(rows)} {noun}, {written} written"
