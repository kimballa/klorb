# © Copyright 2026 Aaron Kimball
"""FilesPanelMixin: the Ctrl+F-toggled Files panel listing every file read or written this
session, across the root session and every subagent beneath it."""

from pathlib import Path

from textual import work
from textual.content import Content
from textual.widgets import OptionList

from klorb.process_config import persist_sidebar
from klorb.session import FileAccessMode
from klorb.tools.util.read_file_core import read_full_file_lines
from klorb.tui._base import ReplAppBase
from klorb.tui.constants import FILES_PANEL_ID, SUBAGENTS_PANEL_ID, TASK_SIDEBAR_ID
from klorb.tui.formatting import render_diff_content, render_full_file_content
from klorb.tui.git_diff import git_diff_hunks_for
from klorb.tui.panels.preview_screens import DiffDetailScreen, ReadDetailScreen
from klorb.tui.widgets.files_panel import FILES_LIST_ID, FileActivityRowData, FilesPanel, FilesPanelOption
from klorb.tui.widgets.subagents_panel import SubagentsPanel
from klorb.tui.widgets.task_sidebar import TaskSidebar


class FilesPanelMixin(ReplAppBase):
    """Ctrl+F shows or hides a docked right-hand panel listing every file this process has read
    or written via `ReadFile`/`EditFile`/`CreateFile`, fed live by `Session.file_accessed()`."""

    def action_toggle_files_panel(self) -> None:
        """Ctrl+F: show or hide the Files panel. Mutually exclusive with the task sidebar and
        the subagents panel since all three dock the same right-hand slot."""
        panel = self.query_one(f"#{FILES_PANEL_ID}", FilesPanel)
        if self._active_sidebar == "files":
            self._active_sidebar = None
            panel.display = False
        else:
            self._show_files_panel(panel)
            self._refresh_files_panel()
            self.query_one(f"#{FILES_LIST_ID}", OptionList).focus()
        persist_sidebar(self._active_sidebar)

    def _show_files_panel(self, panel: FilesPanel) -> None:
        """Make the Files panel the active right-hand sidebar, closing whichever of the task
        sidebar/subagents panel was showing instead."""
        if self._active_sidebar == "tasks":
            self.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar).display = False
        elif self._active_sidebar == "agents":
            self.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel).display = False
        self._active_sidebar = "files"
        panel.display = True

    def _refresh_files_panel(self) -> None:
        """Rebuild the panel's rows from `_file_activity`'s current entries."""
        panel = self.query_one(f"#{FILES_PANEL_ID}", FilesPanel)
        panel.show_rows(self._build_file_activity_rows())

    def _build_file_activity_rows(self) -> list[FileActivityRowData]:
        workspace_root = self._session.config.workspace.path.resolve()
        rows = []
        for entry in self._file_activity.entries():
            rows.append(FileActivityRowData(
                abs_path=entry.abs_path,
                rel_path=_workspace_relative_display(workspace_root, entry.abs_path),
                mode=entry.mode))
        return rows

    def _on_file_accessed(self, path: str, mode: FileAccessMode) -> None:
        """`TurnEventHandlers.on_file_accessed`: record the access, refreshing the panel only
        while it's the visible sidebar. Runs on whichever thread the reporting turn is on."""
        self._file_activity.record(path, mode)
        if self._active_sidebar == "files":
            self.call_from_thread(self._refresh_files_panel)

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != FILES_LIST_ID:
            return
        option = event.option
        assert isinstance(option, FilesPanelOption)
        self._open_file_activity_detail(option.row)

    @work(thread=True)
    def _open_file_activity_detail(self, row: FileActivityRowData) -> None:
        """Reopen `row`'s file: its current full content for a read-only entry, or a freshly
        recomputed diff against its git baseline for a written one. Runs on a worker thread
        since both a file read and `git diff` are synchronous I/O."""
        if row.mode == "write":
            self.call_from_thread(self.push_screen, self._build_diff_screen(row))
        else:
            self.call_from_thread(self.push_screen, self._build_read_screen(row))

    def _build_diff_screen(self, row: FileActivityRowData) -> DiffDetailScreen:
        workspace_root = self._session.config.workspace.path.resolve()
        hunks = git_diff_hunks_for(workspace_root, Path(row.abs_path))
        if hunks is None:
            content: Content = Content("Not inside a git repository; diff unavailable.")
        elif not hunks:
            content = Content("No changes relative to git HEAD.")
        else:
            content = render_diff_content(hunks, max_lines=None)
        return DiffDetailScreen(row.rel_path, content)

    def _build_read_screen(self, row: FileActivityRowData) -> ReadDetailScreen:
        full_view = read_full_file_lines(lambda: open(row.abs_path, encoding="utf-8"), 1)
        if full_view.lines is None:
            content: Content = Content(f"Could not reopen: {full_view.error}")
        else:
            content = render_full_file_content(full_view.lines)
        return ReadDetailScreen(row.rel_path, content, scroll_to_line=1)


def _workspace_relative_display(workspace_root: Path, abs_path: str) -> str:
    """`abs_path` relative to `workspace_root` in posix form, or `abs_path` unchanged if it
    isn't actually under `workspace_root` (a `writeFiles`-granted path outside the workspace)."""
    try:
        return Path(abs_path).resolve().relative_to(workspace_root).as_posix()
    except ValueError:
        return abs_path
