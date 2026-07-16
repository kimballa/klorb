# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.rendering.RenderingMixin."""

import json
from unittest.mock import MagicMock

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static
from tui.conftest import _reply, _session, _session_with_tools, _tool_call_reply, _wait_until

from klorb.session import SessionConfig
from klorb.tui.app import ReplApp
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID
from klorb.tui.mixins.rendering import REASONING_DETAILS_LABEL, THINKING_LABEL, TOOL_USE_LABEL
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.tool_call_widgets import ToolCallStatic


async def test_tool_call_renders_as_a_one_line_summary_by_default() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        tool_call_widgets = list(history.query(ToolCallStatic))
        assert len(tool_call_widgets) == 1
        # EchoTool doesn't override summary(), so the default is just the tool's name.
        assert str(tool_call_widgets[0].render()) == "echo"


async def test_tool_call_widget_is_preceded_by_a_tool_use_label() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        children = list(history.children)
        tool_call_widget = next(w for w in children if isinstance(w, ToolCallStatic))
        label_widget = children[children.index(tool_call_widget) - 1]
        assert isinstance(label_widget, Static)
        assert str(label_widget.render()) == TOOL_USE_LABEL


async def test_ctrl_o_toggles_every_tool_call_widget_including_earlier_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("first done"),
        _tool_call_reply([("call_2", "echo", '{"message": "bye"}')]),
        _reply("second done"),
    ]
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        prompt_input.text = "please echo again"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        tool_call_widgets = list(history.query(ToolCallStatic))
        assert len(tool_call_widgets) == 2
        assert all(str(w.render()) == "echo" for w in tool_call_widgets)

        await pilot.press("ctrl+o")
        await pilot.pause()
        for widget in tool_call_widgets:
            rendered = json.loads(str(widget.render()))
            assert rendered["result"] in ("hi", "bye")

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert all(str(w.render()) == "echo" for w in tool_call_widgets)


async def test_ctrl_o_preserves_the_topmost_visible_history_element() -> None:
    """Toggling tool-call detail must not reset the view to the top of history: whichever
    element was topmost in the viewport before the toggle should still be topmost afterward,
    at the same on-screen line -- not wherever `scroll_y` happens to land once every
    `ToolCallStatic` above it changes height.
    """
    mock_provider = MagicMock()
    tool_calls = [(f"call_{i}", "echo", json.dumps({"message": f"msg{i}"})) for i in range(6)]
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply(tool_calls),
        _reply("all done"),
    ]
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test(size=(80, 10)) as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo six times"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        # Padding well below the last tool call so a mid-content scroll target is never
        # clamped down by `max_scroll_y`, regardless of how tall the mascot banner is.
        for i in range(20):
            history.mount(Static(f"padding line {i}"))
        await pilot.pause()

        tool_call_widgets = list(history.query(ToolCallStatic))
        assert len(tool_call_widgets) == 6

        # Scroll so a middle tool-call widget sits at the very top of the viewport.
        anchor_widget = tool_call_widgets[3]
        target_y = anchor_widget.virtual_region.y
        history.scroll_to(y=target_y, animate=False, immediate=True)
        await pilot.pause()
        assert history.scroll_y == target_y

        await pilot.press("ctrl+o")
        await _wait_until(pilot, lambda: str(anchor_widget.render()) != "echo")
        region = anchor_widget.virtual_region
        assert region.y <= history.scroll_y < region.y + region.height

        await pilot.press("ctrl+o")
        await _wait_until(pilot, lambda: str(anchor_widget.render()) == "echo")
        region = anchor_widget.virtual_region
        assert region.y <= history.scroll_y < region.y + region.height


async def test_unregistered_tool_name_renders_via_default_formatters() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "NoSuchTool", "{}")]),
        _reply("recovered"),
    ]
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "call a bogus tool"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        tool_call_widgets = list(history.query(ToolCallStatic))
        assert len(tool_call_widgets) == 1
        assert str(tool_call_widgets[0].render()).startswith("NoSuchTool: ")


async def test_thinking_chunks_render_as_a_labeled_italicized_block_before_the_response() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ):
        assert on_thinking_chunk is not None
        assert on_chunk is not None
        on_thinking_chunk("Let ")
        on_thinking_chunk("me think.")
        on_chunk("Hello")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        thinking_label = history.query_one(".thinking-label", Static)
        thinking_widgets = list(history.query(".thinking-body").results(Static))
        response_widgets = list(history.query(Markdown))

        assert thinking_label.content == THINKING_LABEL
        assert len(thinking_widgets) == 1
        assert thinking_widgets[0].content == "Let me think."
        assert len(response_widgets) == 1
        assert response_widgets[0].source == "Hello"


async def test_reasoning_details_with_encrypted_entries_renders_a_compact_indicator() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ):
        assert on_reasoning_details is not None
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_thinking_chunk("Let me think.")
        on_reasoning_details([
            {"type": "reasoning.text", "text": "Let me think.", "index": 0},
            {"type": "reasoning.encrypted", "data": "opaque-blob", "index": 1},
        ])
        on_chunk("Hello")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        reasoning_details_label = history.query_one(".reasoning-details-label", Static)
        reasoning_details_widgets = list(history.query(".reasoning-details-body").results(Static))

        assert reasoning_details_label.content == REASONING_DETAILS_LABEL
        assert len(reasoning_details_widgets) == 1
        assert reasoning_details_widgets[0].content == "[2 reasoning blocks preserved, 1 encrypted]"


async def test_reasoning_details_made_only_of_plain_text_entries_renders_nothing() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ):
        assert on_reasoning_details is not None
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_thinking_chunk("Let me think.")
        on_reasoning_details([{"type": "reasoning.text", "text": "Let me think.", "index": 0}])
        on_chunk("Hello")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert list(history.query(".reasoning-details-body").results(Static)) == []


async def test_thinking_chunks_with_multiple_paragraphs_still_render_fully_italicized() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ):
        assert on_thinking_chunk is not None
        assert on_chunk is not None
        on_thinking_chunk("First paragraph.\n\nSecond paragraph.")
        on_chunk("Hello")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        thinking_widgets = list(history.query(".thinking-body").results(Static))

        assert len(thinking_widgets) == 1
        assert thinking_widgets[0].content == "First paragraph.\n\nSecond paragraph."


async def test_thinking_chunks_render_literal_brackets_verbatim() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ):
        assert on_thinking_chunk is not None
        assert on_chunk is not None
        on_thinking_chunk("check [status]")
        on_chunk("Hello")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        thinking_widgets = list(history.query(".thinking-body").results(Static))

        assert thinking_widgets[0].content == "check [status]"
