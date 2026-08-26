# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.files_panel.FilesPanelMixin."""

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

from textual.widgets import OptionList
from tui.conftest import _session

from klorb.session import SessionConfig
from klorb.tui.app import ReplApp
from klorb.tui.constants import FILES_PANEL_ID, SUBAGENTS_PANEL_ID, TASK_SIDEBAR_ID
from klorb.tui.panels.preview_screens import DiffDetailScreen, ReadDetailScreen
from klorb.tui.widgets.files_panel import FILES_LIST_ID, FileActivityRowData, FilesPanel
from klorb.tui.widgets.subagents_panel import SubagentsPanel
from klorb.tui.widgets.task_sidebar import TaskSidebar


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _call_on_a_worker_thread(fn: Callable[[], None]) -> None:
    """`Session.file_accessed()` fires `on_file_accessed` from whichever thread the reporting
    turn is on, so `FilesPanelMixin._on_file_accessed`'s `call_from_thread` marshaling needs a
    real background thread to test against."""
    thread = threading.Thread(target=fn)
    thread.start()
    thread.join(timeout=5.0)


async def test_ctrl_f_shows_then_hides_the_panel(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))

    async with app.run_test() as pilot:
        panel = app.query_one(f"#{FILES_PANEL_ID}", FilesPanel)
        assert bool(panel.display) is False

        await pilot.press("ctrl+f")
        await pilot.pause()
        assert bool(panel.display) is True
        assert app._active_sidebar == "files"

        await pilot.press("ctrl+f")
        await pilot.pause()
        assert bool(panel.display) is False
        assert app._active_sidebar is None


async def test_opening_files_panel_closes_task_sidebar(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))

    async with app.run_test() as pilot:
        await pilot.press("ctrl+t")
        await pilot.pause()
        assert bool(app.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar).display) is True

        await pilot.press("ctrl+f")
        await pilot.pause()
        assert bool(app.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar).display) is False
        assert bool(app.query_one(f"#{FILES_PANEL_ID}", FilesPanel).display) is True


async def test_opening_task_sidebar_closes_files_panel(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))

    async with app.run_test() as pilot:
        await pilot.press("ctrl+f")
        await pilot.pause()
        assert bool(app.query_one(f"#{FILES_PANEL_ID}", FilesPanel).display) is True

        await pilot.press("ctrl+t")
        await pilot.pause()
        assert bool(app.query_one(f"#{FILES_PANEL_ID}", FilesPanel).display) is False
        assert bool(app.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar).display) is True


async def test_opening_files_panel_closes_subagents_panel(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))

    async with app.run_test() as pilot:
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert bool(app.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel).display) is True

        await pilot.press("ctrl+f")
        await pilot.pause()
        assert bool(app.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel).display) is False
        assert bool(app.query_one(f"#{FILES_PANEL_ID}", FilesPanel).display) is True


async def test_on_file_accessed_refreshes_panel_while_visible(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))

    async with app.run_test() as pilot:
        await pilot.press("ctrl+f")
        await pilot.pause()

        _call_on_a_worker_thread(lambda: app._on_file_accessed("/ws/a.txt", "read"))
        await pilot.pause()

        option_list = app.query_one(f"#{FILES_LIST_ID}", OptionList)
        assert option_list.option_count == 1


async def test_on_file_accessed_still_records_while_panel_hidden(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))

    async with app.run_test() as pilot:
        _call_on_a_worker_thread(lambda: app._on_file_accessed("/ws/a.txt", "write"))
        await pilot.pause()

        assert app._file_activity.entries()[0].mode == "write"
        option_list = app.query_one(f"#{FILES_LIST_ID}", OptionList)
        assert option_list.option_count == 0  # not refreshed while hidden

        await pilot.press("ctrl+f")
        await pilot.pause()
        assert option_list.option_count == 1  # populated on show


async def test_build_read_screen_reads_current_file_content(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))
    file_path = tmp_path / "a.txt"
    file_path.write_text("one\ntwo\n")
    row = FileActivityRowData(abs_path=str(file_path), rel_path="a.txt", mode="read")

    async with app.run_test():
        screen = app._build_read_screen(row)

    assert isinstance(screen, ReadDetailScreen)
    assert screen._label == "a.txt"
    assert "two" in str(screen._content)


async def test_build_diff_screen_shows_a_git_diff(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    file_path = tmp_path / "a.txt"
    file_path.write_text("one\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    file_path.write_text("ONE\n")
    row = FileActivityRowData(abs_path=str(file_path), rel_path="a.txt", mode="write")

    async with app.run_test():
        screen = app._build_diff_screen(row)

    assert isinstance(screen, DiffDetailScreen)
    assert "ONE" in str(screen._content)


async def test_build_diff_screen_shows_unchanged_lines_far_from_the_change(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """The diff is shown in the context of the whole file, not windowed hunks: a line far from
    the one change must still appear."""
    app = ReplApp(session=_session(MagicMock(), make_session_config))
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    file_path = tmp_path / "a.txt"
    lines = [str(i) for i in range(1, 41)]
    file_path.write_text("\n".join(lines) + "\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    lines[0] = "CHANGED"
    file_path.write_text("\n".join(lines) + "\n")
    row = FileActivityRowData(abs_path=str(file_path), rel_path="a.txt", mode="write")

    async with app.run_test():
        screen = app._build_diff_screen(row)

    assert isinstance(screen, DiffDetailScreen)
    assert "40" in str(screen._content)  # far past the old fixed 8-line context window


async def test_build_diff_screen_outside_git_repo_returns_none(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))
    file_path = tmp_path / "a.txt"
    file_path.write_text("one\n")
    row = FileActivityRowData(abs_path=str(file_path), rel_path="a.txt", mode="write")

    async with app.run_test():
        screen = app._build_diff_screen(row)

    assert screen is None


async def test_open_file_activity_detail_falls_back_to_read_view_outside_git_repo(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A written entry with no git baseline to diff against still opens, as a plain read."""
    app = ReplApp(session=_session(MagicMock(), make_session_config))
    file_path = tmp_path / "a.txt"
    file_path.write_text("one\n")
    row = FileActivityRowData(abs_path=str(file_path), rel_path="a.txt", mode="write")

    async with app.run_test() as pilot:
        app._open_file_activity_detail(row)
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert isinstance(app.screen, ReadDetailScreen)
