# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.subagents_panel.SubagentsPanelMixin."""

import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

from textual.containers import VerticalScroll
from textual.widgets import Markdown, OptionList, Static
from tui.conftest import _session, _wait_until

from klorb.agents.runtime import SUBAGENT_ABORTED_MARKER, SubagentHandle
from klorb.message import Message, MessageRole, ToolCallRequest
from klorb.session import Session, SessionConfig
from klorb.tui.app import ReplApp
from klorb.tui.constants import (
    HISTORY_ID,
    OUTPUT_TOKENS_ID,
    PROMPT_INPUT_ID,
    SESSION_NAME_ID,
    SUBAGENT_HISTORY_ID,
    SUBAGENTS_PANEL_ID,
    TASK_SIDEBAR_ID,
)
from klorb.tui.formatting import format_token_count
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.subagents_panel import SUBAGENTS_LIST_ID, SubagentsPanel
from klorb.tui.widgets.task_sidebar import TaskSidebar


def _handle(session: Session, role: str = "explorer", title: str = "find the bug") -> SubagentHandle:
    return SubagentHandle(
        session=session, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role=role, title=title)


def _add_subagent(root: Session, role: str = "explorer", title: str = "find the bug") -> SubagentHandle:
    # `session_name=title` mirrors `CreateSubagentTool` pre-setting the child `Session`'s name
    # from `CreateSubagent`'s `session_title` argument -- see docs/specs/subagents.md's
    # "Subagent session model" section.
    child = Session(
        SessionConfig(role_name=role), provider=MagicMock(), parent=root, session_name=title)
    handle = _handle(child, role=role, title=title)
    root.subagent_tracker.register(handle)
    return handle


def _message(content: str, role: MessageRole = "assistant", num_tokens: int = 1) -> Message:
    return Message(
        content=content, role=role, num_tokens=num_tokens, processing_state="complete",
        timestamp=datetime.now())


def _notice_text(app: ReplApp) -> str:
    notice = app._subagent_transcript_notice
    assert notice is not None
    return str(notice.render())


def _pending_interrupt(app: ReplApp) -> str | None:
    return app._subagent_interrupt_pending


async def test_ctrl_g_shows_then_hides_the_panel() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        panel = app.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel)
        assert bool(panel.display) is False

        await pilot.press("ctrl+g")
        await pilot.pause()
        assert bool(panel.display) is True

        await pilot.press("ctrl+g")
        await pilot.pause()
        assert bool(panel.display) is False


async def test_ctrl_g_hides_the_task_sidebar_if_shown() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press("ctrl+t")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app._active_sidebar == "tasks"

        await pilot.press("ctrl+g")
        await pilot.pause()

        assert app._active_sidebar == "agents"
        assert bool(app.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar).display) is False


async def test_ctrl_t_hides_the_subagents_panel_if_shown() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert app._active_sidebar == "agents"

        await pilot.press("ctrl+t")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert app._active_sidebar == "tasks"
        assert bool(app.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel).display) is False


async def test_selecting_a_subagent_swaps_history_visibility_and_disables_prompt_input() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()

        assert app._selected_session is handle.session
        assert app._selected_handle is handle
        assert not app.query_one(f"#{HISTORY_ID}", VerticalScroll).display
        assert app.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll).display
        assert app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).disabled


async def test_reselecting_the_root_restores_history_and_re_enables_prompt_input() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()

        app._select_session(session.id)
        await pilot.pause()

        assert app._selected_session is session
        assert app._selected_handle is None
        assert app.query_one(f"#{HISTORY_ID}", VerticalScroll).display
        assert not app.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll).display
        assert not app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).disabled


async def test_selecting_a_row_via_the_option_list_switches_selection() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+g")
        await pilot.pause()

        option_list = app.query_one(f"#{SUBAGENTS_LIST_ID}", OptionList)
        assert option_list.highlighted == 0  # root row, selected by default

        option_list.highlighted = 1  # the subagent row
        await pilot.pause()

        assert app._selected_session is handle.session


async def test_escape_aborts_the_selected_subagents_own_cancel_event() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert handle.cancel_event.is_set()
        assert app._cancel_event is None  # the root session's own turn state is untouched


async def test_root_row_never_shows_a_running_marker_but_footer_shows_its_role() -> None:
    session = _session(MagicMock())
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        await pilot.press("ctrl+g")
        await pilot.pause()

        panel = app.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel)
        option_list = panel.query_one(f"#{SUBAGENTS_LIST_ID}", OptionList)
        assert option_list.option_count == 1


async def test_new_subagent_messages_stay_pinned_to_the_bottom_when_already_there() -> None:
    """See `test_streaming_updates_stay_pinned_to_the_bottom_when_the_user_is_at_the_bottom` in
    test_prompt_submission.py for why this spies on `_scroll_if_pinned` rather than asserting on
    the viewport's actual scroll position."""
    session = _session(MagicMock())
    handle = _add_subagent(session)
    handle.session.load_messages([_message(f"padding line {i}") for i in range(20)])
    app = ReplApp(session=session)

    async with app.run_test(size=(40, 6)) as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()
        assert app._subagent_history_pinned_to_bottom is True

        with patch.object(ReplApp, "_scroll_if_pinned", autospec=True) as mock_scroll_if_pinned:
            handle.session.load_messages([*handle.session.messages, _message("new content")])
            app._append_new_subagent_messages(handle.session, handle)

        assert mock_scroll_if_pinned.call_args_list
        assert all(call.args[-1] for call in mock_scroll_if_pinned.call_args_list)


async def test_new_subagent_messages_do_not_yank_the_scroll_when_the_user_has_scrolled_away() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    handle.session.load_messages([_message(f"padding line {i}") for i in range(20)])
    app = ReplApp(session=session)

    async with app.run_test(size=(40, 6)) as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()

        container = app.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        container.scroll_home(animate=False)
        await _wait_until(pilot, lambda: not app._subagent_history_pinned_to_bottom)

        with patch.object(ReplApp, "_scroll_if_pinned", autospec=True) as mock_scroll_if_pinned:
            handle.session.load_messages([*handle.session.messages, _message("new content")])
            app._append_new_subagent_messages(handle.session, handle)

        assert mock_scroll_if_pinned.call_args_list
        assert not any(call.args[-1] for call in mock_scroll_if_pinned.call_args_list)


async def test_status_bar_reflects_the_selected_subagent_then_reverts_to_the_root() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    handle.session.load_messages([_message("subagent reply", num_tokens=42)])
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        output_tokens = app.query_one(f"#{OUTPUT_TOKENS_ID}", Static)
        root_text = str(output_tokens.render())

        app._select_session(handle.session.id)
        await pilot.pause()
        assert str(output_tokens.render()) == f"↓ {format_token_count(42)}"

        app._select_session(session.id)
        await pilot.pause()
        assert str(output_tokens.render()) == root_text


async def test_subagent_notice_reads_task_complete_for_an_ordinary_finish() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()
        assert _notice_text(app) == "Subagent is still working…"

        handle.state = "finished"
        handle.output = "done"
        app._append_new_subagent_messages(handle.session, handle)

        assert _notice_text(app) == "Subagent task complete."


async def test_subagent_notice_reads_interrupted_for_an_aborted_finish() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    handle.state = "finished"
    handle.output = f"partial output\n\n{SUBAGENT_ABORTED_MARKER}"
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()

        assert _notice_text(app) == "Subagent interrupted."


async def test_escape_immediately_shows_sending_interrupt_then_flips_to_interrupted() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()

        assert _pending_interrupt(app) == handle.session.id
        assert _notice_text(app) == "Sending interrupt…"

        # The subagent's background thread notices `cancel_event` and finishes.
        handle.state = "finished"
        handle.output = f"partial output\n\n{SUBAGENT_ABORTED_MARKER}"
        app._append_new_subagent_messages(handle.session, handle)

        assert _pending_interrupt(app) is None
        assert _notice_text(app) == "Subagent interrupted."


async def test_header_title_follows_selection_to_the_subagents_own_model() -> None:
    session = _session(MagicMock(), model="root/model")
    handle = _add_subagent(session)
    handle.session.config.model = "subagent/model"
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        assert "root/model" in app.format_title(app.title, app.sub_title).plain

        app._select_session(handle.session.id)
        await pilot.pause()

        assert "subagent/model" in app.format_title(app.title, app.sub_title).plain
        assert "root/model" not in app.format_title(app.title, app.sub_title).plain


async def test_session_name_line_follows_selection_to_the_subagents_own_title() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session, title="find the flaky test")
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()

        session_name = app.query_one(f"#{SESSION_NAME_ID}", Static)
        assert str(session_name.render()) == "Session: find the flaky test"

        app._select_session(session.id)
        await pilot.pause()

        session_name = app.query_one(f"#{SESSION_NAME_ID}", Static)
        assert "find the flaky test" not in str(session_name.render())


async def test_transcript_renders_a_tool_use_messages_own_commentary_alongside_its_tool_calls() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    handle.session.load_messages([
        Message(
            content="Here is the final answer.", role="tool_use", num_tokens=1,
            processing_state="complete", timestamp=datetime.now(),
            tool_calls=[ToolCallRequest(id="call-1", name="SampleTool", arguments="{}")]),
    ])
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()

        container = app.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        responses = list(container.query(Markdown))
        assert any(widget.source == "Here is the final answer." for widget in responses)


async def test_transcript_reconstructs_an_empty_thinking_body_from_reasoning_details() -> None:
    session = _session(MagicMock())
    handle = _add_subagent(session)
    handle.session.load_messages([
        Message(
            content="", role="thinking", num_tokens=1, processing_state="complete",
            timestamp=datetime.now(),
            reasoning_details=[{"type": "reasoning.text", "text": "the real reasoning"}]),
    ])
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        app._select_session(handle.session.id)
        await pilot.pause()

        container = app.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        bodies = list(container.query(".thinking-body"))
        assert any("the real reasoning" in str(widget.render()) for widget in bodies)
