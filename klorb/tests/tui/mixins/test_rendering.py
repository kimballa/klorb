# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.rendering.RenderingMixin."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static
from tui.conftest import TEST_SESSION_ID, _reply, _session, _session_with_tools, _tool_call_reply, _wait_until

from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.registry import ToolRegistry
from klorb.tui.app import ReplApp
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID
from klorb.tui.mixins.rendering import REASONING_DETAILS_LABEL, THINKING_LABEL, TOOL_USE_LABEL
from klorb.tui.panels.preview_screens import DiffDetailScreen, ReadDetailScreen
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.tool_call_widgets import ToolCallStatic
from klorb.workspace import Workspace


def _session_with_real_tools(provider: MagicMock, config: SessionConfig) -> Session:
    """Like `tui.conftest._session_with_tools`, but discovers the real production tool package
    (`klorb.tools`, `ToolRegistry.discover_tools`'s default) instead of the test-fixture
    `sample_tools` package -- needed for these tests, which exercise real `EditFile`/`ReadFile`
    diff/read previews rather than `sample_tools.echo_tool`'s plain-string rendering."""
    process_config = ProcessConfig()
    tool_registry = ToolRegistry.discover_tools(process_config, config)
    return Session(
        config, provider=provider, session_id=TEST_SESSION_ID, tool_registry=tool_registry,
        process_config=process_config)


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


async def test_edit_file_call_renders_a_colored_diff_preview_clickable_to_full_overlay(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("a\nb\nc\n")
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "EditFile", json.dumps({
            "filename": str(file_path), "start_line": 2, "end_line": 2,
            "start_text": "b", "end_text": "b", "new_text": "B",
        }))]),
        _reply("edited"),
    ]
    session_config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path),
        read_dirs=DirRules(allow=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]))
    session = _session_with_real_tools(mock_provider, session_config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "edit the file"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        tool_call_widgets = list(history.query(ToolCallStatic))
        assert len(tool_call_widgets) == 1
        widget = tool_call_widgets[0]
        rendered = str(widget.render())
        assert rendered.startswith(f"Edit file: {file_path}")
        assert "- b" in rendered
        assert "+ B" in rendered
        # Unchanged context lines from the edit still show, unstyled.
        assert "a" in rendered
        assert "c" in rendered

        await pilot.click(widget)
        await pilot.pause()
        assert isinstance(app.screen, DiffDetailScreen)

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, DiffDetailScreen)


async def test_ctrl_o_shows_the_full_diff_for_an_edit_file_call(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("\n".join(str(i) for i in range(1, 31)) + "\n")
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "EditFile", json.dumps({
            "filename": str(file_path), "start_line": 15, "end_line": 15,
            "start_text": "15", "end_text": "15", "new_text": "X",
        }))]),
        _reply("edited"),
    ]
    session_config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path),
        read_dirs=DirRules(allow=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]))
    session = _session_with_real_tools(mock_provider, session_config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "edit the file"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        widget = history.query_one(ToolCallStatic)
        compact_rendered = str(widget.render())
        # The change itself must still be visible in the capped compact view: with a full
        # 8-line leading context ahead of it (line 15 has 8+ lines of untouched context on both
        # sides in this 30-line file), an 8-line cap that started at the hunk's own beginning
        # would show nothing but that context and never reach the change at all -- it should
        # instead start only 2 lines before the change (context lines 13-14), fill the rest of
        # its 8-line budget with the change and as much trailing context as fits, and truncate.
        # 1 header + (context 13, 14; del 15; add 15; context 16-19) + "..." = 10 lines.
        assert compact_rendered.count("\n") + 1 == 10
        assert "- 15" in compact_rendered
        assert "+ X" in compact_rendered
        assert compact_rendered.rstrip().endswith("...")

        await pilot.press("ctrl+o")
        await pilot.pause()
        full_rendered = str(widget.render())
        # The full view reaches all the way back to the hunk's true leading context: 1 header +
        # (context 7-14; del 15; add 15; context 16-23), no truncation marker.
        assert full_rendered.count("\n") + 1 == 19
        assert "- 15" in full_rendered
        assert "+ X" in full_rendered
        assert "..." not in full_rendered

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert str(widget.render()) == compact_rendered  # back to compact, unchanged


async def test_read_file_call_renders_a_numbered_preview_clickable_to_full_file_overlay(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("\n".join(f"line {i}" for i in range(1, 11)) + "\n")
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "ReadFile", json.dumps({"filename": str(file_path)}))]),
        _reply("read"),
    ]
    session_config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path),
        read_dirs=DirRules(allow=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]))
    session = _session_with_real_tools(mock_provider, session_config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "read the file"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        widget = history.query_one(ToolCallStatic)
        rendered = str(widget.render())
        assert rendered.startswith(f"Read file: {file_path}")
        assert "line 1" in rendered
        assert "line 4" in rendered
        assert "line 5" not in rendered  # only the first 4 lines preview inline
        assert rendered.rstrip().endswith("...")

        await pilot.click(widget)
        await pilot.pause()
        assert isinstance(app.screen, ReadDetailScreen)
        overlay_text = str(app.screen.query_one("#preview-detail-label", Static).render())
        assert "Read file:" in overlay_text

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ReadDetailScreen)


async def test_read_file_call_overlay_reports_an_error_if_the_file_is_gone(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("line 1\nline 2\n")
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "ReadFile", json.dumps({"filename": str(file_path)}))]),
        _reply("read"),
    ]
    session_config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path),
        read_dirs=DirRules(allow=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]))
    session = _session_with_real_tools(mock_provider, session_config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "read the file"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        widget = history.query_one(ToolCallStatic)
        file_path.unlink()

        await pilot.click(widget)
        await pilot.pause()
        assert isinstance(app.screen, ReadDetailScreen)
        content_text = str(app.screen.query_one("#preview-detail-content", Static).render())
        assert "Could not reopen" in content_text


async def test_detail_screen_header_label_stays_pinned_while_content_scrolls(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("\n".join(f"line {i}" for i in range(1, 201)) + "\n")
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "ReadFile", json.dumps({"filename": str(file_path)}))]),
        _reply("read"),
    ]
    session_config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path),
        read_dirs=DirRules(allow=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]))
    session = _session_with_real_tools(mock_provider, session_config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "read the file"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        widget = history.query_one(ToolCallStatic)
        await pilot.click(widget)
        await pilot.pause()
        assert isinstance(app.screen, ReadDetailScreen)

        label = app.screen.query_one("#preview-detail-label", Static)
        scroll = app.screen.query_one("#preview-detail-scroll", VerticalScroll)
        label_y_before = label.region.y

        scroll.scroll_to(y=50, animate=False)
        await pilot.pause()

        assert scroll.scroll_y > 0
        assert label.region.y == label_y_before
