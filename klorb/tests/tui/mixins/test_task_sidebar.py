# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.task_sidebar.TaskSidebarMixin."""

from typing import Any
from unittest.mock import MagicMock, patch

from textual.widgets import Static
from tui.conftest import _session

from klorb.session import ToolCallEvent
from klorb.tui.app import ReplApp
from klorb.tui.constants import TASK_SIDEBAR_ID
from klorb.tui.widgets.task_sidebar import _BODY_ID, TaskSidebar


def _issue(issue_id: int, title: str, status: str = "open") -> dict[str, Any]:
    return {"id": issue_id, "title": title, "status": status, "blocked_by": []}


def _sidebar_body_text(app: ReplApp) -> str:
    sidebar = app.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar)
    return str(sidebar.query_one(f"#{_BODY_ID}", Static).render())


async def test_ctrl_t_shows_then_hides_the_sidebar() -> None:
    app = ReplApp(session=_session(MagicMock()))

    with patch("klorb.tui.mixins.task_sidebar.chainlink_available", return_value=False):
        async with app.run_test() as pilot:
            sidebar = app.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar)
            assert bool(sidebar.display) is False

            await pilot.press("ctrl+t")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert bool(sidebar.display) is True

            await pilot.press("ctrl+t")
            await pilot.pause()
            assert bool(sidebar.display) is False


async def test_ctrl_t_shows_unavailable_message_when_no_chainlink_binary() -> None:
    app = ReplApp(session=_session(MagicMock()))

    with patch("klorb.tui.mixins.task_sidebar.chainlink_available", return_value=False):
        async with app.run_test() as pilot:
            await pilot.press("ctrl+t")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert "not available" in _sidebar_body_text(app)


async def test_ctrl_t_lists_open_and_closed_tasks_starring_the_current_one() -> None:
    session = _session(MagicMock())
    session.set_chainlink_task(2)
    app = ReplApp(session=session)
    issues = [_issue(2, "In progress"), _issue(1, "Done", status="closed")]

    with (
        patch("klorb.tui.mixins.task_sidebar.chainlink_available", return_value=True),
        patch("klorb.tui.mixins.task_sidebar.ChainlinkClient"),
        patch("klorb.tui.mixins.task_sidebar.fetch_and_sort_issues", return_value=issues),
    ):
        async with app.run_test() as pilot:
            await pilot.press("ctrl+t")
            await app.workers.wait_for_complete()
            await pilot.pause()

            body = _sidebar_body_text(app)
            assert "★ #2 In progress" in body
            assert "#1 Done" in body


async def test_relevant_tool_call_refreshes_sidebar_while_shown() -> None:
    session = _session(MagicMock())
    app = ReplApp(session=session)

    with (
        patch("klorb.tui.mixins.task_sidebar.chainlink_available", return_value=True),
        patch("klorb.tui.mixins.task_sidebar.ChainlinkClient"),
        patch(
            "klorb.tui.mixins.task_sidebar.fetch_and_sort_issues",
            return_value=[_issue(1, "First fetch")],
        ) as mock_fetch,
    ):
        async with app.run_test() as pilot:
            await pilot.press("ctrl+t")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert mock_fetch.call_count == 1

            mock_fetch.return_value = [_issue(1, "Second fetch")]
            app._maybe_refresh_task_sidebar_after_tool_call(
                ToolCallEvent(call_id="c1", name="TodoNext", args={}, result={}))
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert mock_fetch.call_count == 2
            assert "Second fetch" in _sidebar_body_text(app)


async def test_irrelevant_tool_call_does_not_refresh_sidebar() -> None:
    session = _session(MagicMock())
    app = ReplApp(session=session)

    with (
        patch("klorb.tui.mixins.task_sidebar.chainlink_available", return_value=True),
        patch("klorb.tui.mixins.task_sidebar.ChainlinkClient"),
        patch(
            "klorb.tui.mixins.task_sidebar.fetch_and_sort_issues", return_value=[],
        ) as mock_fetch,
    ):
        async with app.run_test() as pilot:
            await pilot.press("ctrl+t")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert mock_fetch.call_count == 1

            app._maybe_refresh_task_sidebar_after_tool_call(
                ToolCallEvent(call_id="c1", name="ReadFile", args={}, result={}))
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert mock_fetch.call_count == 1
