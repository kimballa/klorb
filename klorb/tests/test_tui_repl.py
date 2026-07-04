# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.repl."""

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Literal
from unittest.mock import MagicMock
from unittest.mock import patch

import fixtures.sample_models as sample_models_package
import fixtures.sample_tools as sample_tools_package
import pytest
from textual.containers import VerticalScroll
from textual.widgets import Input
from textual.widgets import Markdown
from textual.widgets import OptionList
from textual.widgets import Static

from klorb.api_provider import ProviderResponse
from klorb.api_provider import ResponseAborted
from klorb.logging_config import session_log_path
from klorb.message import Message
from klorb.message import ToolCallRequest
from klorb.models.registry import ModelRegistry
from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session import PermissionAskContext
from klorb.session import PermissionDecision
from klorb.session import Session
from klorb.session import SessionConfig
from klorb.tools.registry import ToolRegistry
from klorb.tui.permission_ask_screen import PERMISSION_ASK_INPUT_ID
from klorb.tui.permission_ask_screen import PERMISSION_ASK_OPTIONS_ID
from klorb.tui.permission_ask_screen import PermissionAskScreen
from klorb.tui.repl import HISTORY_ID
from klorb.tui.repl import PROMPT_INPUT_ID
from klorb.tui.repl import STATUS_BAR_ID
from klorb.tui.repl import THINKING_LABEL
from klorb.tui.repl import TOOL_USE_LABEL
from klorb.tui.repl import PromptInput
from klorb.tui.repl import ReplApp
from klorb.tui.repl import ToolCallLimitScreen
from klorb.tui.repl import ToolCallStatic
from klorb.tui.repl import format_token_count


TEST_SESSION_ID = "test-session-id"


def _session(provider: MagicMock, model: str = "some/model") -> Session:
    return Session(SessionConfig(model=model), provider=provider, session_id=TEST_SESSION_ID)


def _session_with_tools(
    provider: MagicMock, config: SessionConfig, process_config: ProcessConfig | None = None,
) -> Session:
    tool_registry = ToolRegistry(process_config or ProcessConfig(), config, package=sample_tools_package)
    return Session(
        config, provider=provider, session_id=TEST_SESSION_ID, tool_registry=tool_registry,
        process_config=process_config)


def _reply(content: str = "model reply") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content,
            role="assistant",
            num_tokens=1,
            processing_state="complete",
            timestamp=datetime.now(),
        ),
        prompt_tokens=1,
    )


def _tool_call_reply(calls: list[tuple[str, str, str]]) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="",
            role="assistant",
            num_tokens=1,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=id_, name=name, arguments=args) for id_, name, args in calls],
        ),
        prompt_tokens=1,
    )


def test_format_token_count_examples() -> None:
    assert format_token_count(0) == "0"
    assert format_token_count(500) == "500"
    assert format_token_count(999) == "999"
    assert format_token_count(1000) == "1k"
    assert format_token_count(1400) == "1.4k"
    assert format_token_count(23000) == "23k"
    assert format_token_count(24000) == "24k"
    assert format_token_count(25000) == "25k"
    assert format_token_count(200000) == "200k"
    assert format_token_count(210000) == "210k"
    assert format_token_count(220000) == "220k"
    assert format_token_count(423000) == "420k"
    assert format_token_count(1_000_000) == "1M"


async def test_status_bar_shows_zero_tokens_against_model_context_window_on_mount() -> None:
    mock_provider = MagicMock()
    registry = ModelRegistry(package=sample_models_package)
    session = Session(
        SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry,
        session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test():
        status_bar = app.query_one(f"#{STATUS_BAR_ID}", Static)
        assert status_bar.content == "0 / 8k"


async def test_status_bar_updates_after_a_turn_completes() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    session = Session(
        SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry,
        session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        status_bar = app.query_one(f"#{STATUS_BAR_ID}", Static)
        assert status_bar.content == f"{session.total_tokens_used()} / 8k"


async def test_status_bar_omits_limit_when_model_unregistered() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        status_bar = app.query_one(f"#{STATUS_BAR_ID}", Static)
        assert status_bar.content == "0"


async def test_submitting_a_prompt_shows_it_and_the_response() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "what is 2+2?"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = history.query_one(Static)
        response_widget = history.query_one(Markdown)

        assert prompt_widget.content == "what is 2+2?"
        assert response_widget.source == "model reply"
        assert prompt_input.text == ""

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["model"] == "some/model"
    assert kwargs["session_id"] == TEST_SESSION_ID


async def test_submitting_an_empty_prompt_does_nothing() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "   "
        await pilot.press("enter")
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(history.children) == 0

    mock_provider.send_prompt.assert_not_called()


async def test_provider_error_is_shown_in_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = RuntimeError("boom")
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        error_widget = history.query_one(".error", Static)

        assert "boom" in str(error_widget.content)
        assert prompt_input.disabled is False


async def test_aborted_response_restores_prompt_and_clears_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = ResponseAborted()
    session = _session(mock_provider)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "what is 2+2?"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(history.children) == 0
        assert prompt_input.text == "what is 2+2?"
        assert prompt_input.disabled is False

    # Only the system bookkeeping message (inserted ahead of the turn) survives the abort;
    # the discarded turn itself leaves nothing behind.
    assert [m.role for m in session.messages] == ["system"]


async def test_escape_aborts_a_streaming_response() -> None:
    mock_provider = MagicMock()
    streaming_started = threading.Event()

    def fake_send_prompt(*args: Any, cancel_event: threading.Event | None = None, **kwargs: Any) -> Any:
        assert cancel_event is not None
        streaming_started.set()
        cancel_event.wait(timeout=5)
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = _session(mock_provider)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "what is 2+2?"
        await pilot.press("enter")

        while not streaming_started.is_set():
            await asyncio.sleep(0.01)
        await pilot.pause()
        assert app.check_action("abort_response", ()) is True

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(history.children) == 0
        assert prompt_input.text == "what is 2+2?"
        assert prompt_input.disabled is False
        assert app.check_action("abort_response", ()) is False

    # Only the system bookkeeping message (inserted ahead of the turn) survives the abort.
    assert [m.role for m in session.messages] == ["system"]


async def test_select_model_updates_active_model_and_subtitle() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply("ok")
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.select_model("other/model")
        await pilot.pause()
        assert app.sub_title == "other/model"

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["model"] == "other/model"


async def test_set_thinking_enabled_updates_session_config() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_enabled(False)
        assert app._session.config.thinking_enabled is False

        app.set_thinking_enabled(True)
        assert app._session.config.thinking_enabled is True


async def test_set_thinking_effort_updates_session_config() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_effort("low")
        assert app._session.config.thinking_effort == "low"


async def test_prompt_input_max_height_comes_from_process_config() -> None:
    mock_provider = MagicMock()
    process_config = ProcessConfig(prompt_input_max_lines=3)
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test():
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        max_height = prompt_input.styles.max_height
        assert max_height is not None
        assert int(max_height.value) == 4


async def test_select_model_also_updates_process_config_template() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.select_model("other/model")
        assert app._process_config.session.model == "other/model"


async def test_set_thinking_enabled_also_updates_process_config_template() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_enabled(False)
        assert app._process_config.session.thinking_enabled is False


async def test_set_thinking_effort_also_updates_process_config_template() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_effort("low")
        assert app._process_config.session.thinking_effort == "low"


async def test_get_thinking_effort_reflects_session_config() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_effort("low")
        assert app.get_thinking_effort() == "low"


async def test_initial_message_is_submitted_as_first_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider), initial_message="what is 2+2?")

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = history.query_one(Static)
        response_widget = history.query_one(Markdown)

        assert prompt_widget.content == "what is 2+2?"
        assert response_widget.source == "model reply"

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        assert prompt_input.disabled is False

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["model"] == "some/model"
    assert kwargs["session_id"] == TEST_SESSION_ID


async def test_default_session_gets_a_tool_registry_discovering_built_in_tools() -> None:
    app = ReplApp()

    async with app.run_test():
        assert app._session.tool_registry is not None
        assert "ReadFile" in {tool.name() for tool in app._session.tool_registry.tools()}


async def test_tool_call_limit_modal_yes_continues_the_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=0)
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")

        while len(app.screen_stack) < 2:
            await asyncio.sleep(0.01)
        await pilot.pause()
        assert isinstance(app.screen, ToolCallLimitScreen)

        await pilot.click("#tool-call-limit-yes")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        response_widget = app.query_one(f"#{HISTORY_ID}", VerticalScroll).children[-1]
        assert isinstance(response_widget, Markdown)
        assert response_widget.source == "final answer"


async def test_tool_call_limit_modal_no_shows_error() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=0)
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")

        while len(app.screen_stack) < 2:
            await asyncio.sleep(0.01)
        await pilot.pause()
        assert isinstance(app.screen, ToolCallLimitScreen)

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        error_widget = app.query_one(f"#{HISTORY_ID}", VerticalScroll).children[-1]
        assert isinstance(error_widget, Static)
        assert "0 tool call" in str(error_widget.render())


# --- tool call rendering ---


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


async def test_ctrl_o_footer_label_reads_detail_then_hide() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        assert app.active_bindings["ctrl+o"].binding.description == "Detail"

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.active_bindings["ctrl+o"].binding.description == "Hide"

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.active_bindings["ctrl+o"].binding.description == "Detail"


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


async def test_aborting_a_turn_removes_its_tool_call_widgets() -> None:
    mock_provider = MagicMock()
    streaming_started = threading.Event()
    calls_made = 0

    def fake_send_prompt(*args: Any, cancel_event: threading.Event | None = None, **kwargs: Any) -> Any:
        nonlocal calls_made
        calls_made += 1
        if calls_made == 1:
            return _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
        assert cancel_event is not None
        streaming_started.set()
        cancel_event.wait(timeout=5)
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")

        while not streaming_started.is_set():
            await asyncio.sleep(0.01)
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(list(history.query(ToolCallStatic))) == 1

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(list(history.query(ToolCallStatic))) == 0
        assert app._tool_call_widgets == []


# --- shell commands ("!"-prefixed) ---


async def test_shell_command_echoes_and_shows_output() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!echo hello"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        widgets = list(history.query(Static))
        assert widgets[0].content == "!echo hello"
        assert widgets[1].content == "hello\n"
        assert prompt_input.disabled is False
        assert prompt_input.text == ""

    mock_provider.send_prompt.assert_not_called()


async def test_shell_command_preserves_newlines_across_multiple_lines() -> None:
    """Regression test: shell output used to be rendered via a `Markdown` widget, whose
    CommonMark rendering collapses a single newline inside a paragraph into a soft line
    break (a space). Shell output isn't markdown, so its newlines must survive verbatim.
    """
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!printf 'a\\nb\\nc\\n'"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        output_widget = list(history.query(Static))[1]
        assert output_widget.content == "a\nb\nc\n"


async def test_shell_command_exit_code_shown_as_error() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!exit 7"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        error_widget = history.query_one(".error", Static)
        assert "status 7" in str(error_widget.content)
        assert prompt_input.disabled is False


async def test_shell_command_uses_configured_shell_binary() -> None:
    mock_provider = MagicMock()
    process_config = ProcessConfig(shell_command="/no/such/shell")
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!echo hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        output_widget = list(history.query(Static))[1]
        assert "/no/such/shell not found" in str(output_widget.content)


async def test_shell_command_timeout_kills_it_and_shows_an_error() -> None:
    mock_provider = MagicMock()
    process_config = ProcessConfig(shell_timeout_seconds=0.2)
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!sleep 5"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        error_widget = history.query_one(".error", Static)
        assert "timed out" in str(error_widget.content)
        assert prompt_input.disabled is False


async def test_shell_command_disables_input_and_ctrl_c_interrupts_it(tmp_path: Path) -> None:
    """A running shell command disables the input box (enforcing "only one at a time"), and
    Ctrl+C — which otherwise quits the app — kills the in-flight shell command instead.
    """
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))
    marker = tmp_path / "started"

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = f"!touch {marker}; sleep 5"
        await pilot.press("enter")

        while not marker.exists():
            await asyncio.sleep(0.01)
        await pilot.pause()

        assert prompt_input.disabled is True
        assert app._shell_cancel_event is not None

        await pilot.press("ctrl+c")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        error_widget = history.query_one(".error", Static)
        assert "interrupted" in str(error_widget.content)
        assert prompt_input.disabled is False
        assert app._shell_cancel_event is None  # type: ignore[unreachable]
        assert app.is_running


async def test_ctrl_c_quits_when_no_shell_command_is_running() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.exit = MagicMock()  # type: ignore[method-assign]
        await pilot.press("ctrl+c")
        await pilot.pause()

        app.exit.assert_called_once()


def _ask_permission_call(id_: str, path: Path, *, is_write: bool = True) -> tuple[str, str, str]:
    return id_, "ask_permission", json.dumps({"path": str(path), "is_write": is_write})


# --- PermissionAskScreen (unit-level, mirroring ThinkingEffortScreen's test style) ---


def _ask_ctx(tmp_path: Path, is_write: bool = True) -> PermissionAskContext:
    target = tmp_path / "f.txt"
    return PermissionAskContext(path=target, is_write=is_write, resource_description=f"write to {target}")


def test_permission_ask_screen_message_names_the_granted_directory(tmp_path: Path) -> None:
    screen = PermissionAskScreen(_ask_ctx(tmp_path), granted_paths=[tmp_path])

    container = next(iter(screen.compose()))
    message, _ = container._pending_children

    assert isinstance(message, Static)
    assert str(tmp_path) in str(message.render())


def test_permission_ask_screen_lists_all_six_options_in_order(tmp_path: Path) -> None:
    screen = PermissionAskScreen(_ask_ctx(tmp_path), granted_paths=[tmp_path])

    container = next(iter(screen.compose()))
    _, option_list = container._pending_children

    assert isinstance(option_list, OptionList)
    prompts = tuple(str(option.prompt) for option in option_list._options)
    assert prompts == (
        "Allow (once)", "Allow (this session)", "Allow (always, in this workspace)",
        "Allow (always, for me)", "Deny", "Other...")


@pytest.mark.parametrize(("option_index", "expected_choice"), [
    (0, "once"), (1, "session"), (2, "workspace"), (3, "homedir"), (4, "deny"),
])
def test_on_option_list_option_selected_dismisses_with_matching_choice(
    tmp_path: Path, option_index: int,
    expected_choice: Literal["once", "session", "workspace", "homedir", "deny"],
) -> None:
    screen = PermissionAskScreen(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(option_index=option_index)

    screen.on_option_list_option_selected(event)

    screen.dismiss.assert_called_once_with(PermissionDecision(choice=expected_choice))


def test_selecting_other_reveals_input_instead_of_dismissing(tmp_path: Path) -> None:
    screen = PermissionAskScreen(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    screen._reveal_other_input = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(option_index=5)

    screen.on_option_list_option_selected(event)

    screen._reveal_other_input.assert_called_once()
    screen.dismiss.assert_not_called()


def test_on_input_submitted_dismisses_with_other_choice_and_text(tmp_path: Path) -> None:
    screen = PermissionAskScreen(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(value="use /tmp instead")

    screen.on_input_submitted(event)

    screen.dismiss.assert_called_once_with(PermissionDecision(choice="other", other_text="use /tmp instead"))


def test_action_decline_dismisses_with_deny(tmp_path: Path) -> None:
    screen = PermissionAskScreen(_ask_ctx(tmp_path), granted_paths=[tmp_path])
    screen.dismiss = MagicMock()  # type: ignore[method-assign]

    screen.action_decline()

    screen.dismiss.assert_called_once_with(PermissionDecision(choice="deny"))


# --- PermissionAskScreen end-to-end through ReplApp ---


async def test_permission_ask_modal_appears_for_an_ask_tool_call(tmp_path: Path) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", tmp_path / "f.txt")]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace_root=tmp_path)
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        while len(app.screen_stack) < 2:
            await asyncio.sleep(0.01)
        await pilot.pause()

        assert isinstance(app.screen, PermissionAskScreen)


async def test_permission_ask_modal_escape_denies_and_shows_error(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace_root=tmp_path)
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        while len(app.screen_stack) < 2:
            await asyncio.sleep(0.01)
        await pilot.pause()
        assert isinstance(app.screen, PermissionAskScreen)

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "Permission denied" in str(tool_response.content)
        assert config.read_dirs == DirRules()
        assert config.write_dirs == DirRules()


async def test_permission_ask_modal_session_scope_grants_and_retries(tmp_path: Path) -> None:
    """Full plumbing check: selecting "Allow (this session)" applies the grant to the live
    session config (`Session._retry_after_permission_decision` -> `apply_permission_grant`,
    once `_on_permission_ask` reports the user's choice) and the retried tool call succeeds --
    but the process-config template is untouched, since "session" scope is in-memory-only,
    narrower than "workspace"/"homedir"."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace_root=tmp_path)
    process_config = ProcessConfig(session=SessionConfig(model="some/model", workspace_root=tmp_path))
    session = _session_with_tools(mock_provider, config, process_config)
    app = ReplApp(session=session, process_config=process_config)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        while len(app.screen_stack) < 2:
            await asyncio.sleep(0.01)
        await pilot.pause()
        assert isinstance(app.screen, PermissionAskScreen)

        screen = app.screen
        assert isinstance(screen, PermissionAskScreen)
        screen.dismiss(PermissionDecision(choice="session"))
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        response_widget = app.query_one(f"#{HISTORY_ID}", VerticalScroll).children[-1]
        assert isinstance(response_widget, Markdown)
        assert response_widget.source == "final answer"
        assert config.read_dirs.allow == [target.parent]
        assert config.write_dirs.allow == [target.parent]
        # "session" scope is in-memory-only for the live session -- the process-config
        # template (what a future /clear would copy from) must stay untouched.
        assert process_config.session.read_dirs == DirRules()
        assert process_config.session.write_dirs == DirRules()


async def test_permission_ask_modal_other_reveals_input_and_denial_includes_text(
    tmp_path: Path,
) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model", workspace_root=tmp_path)
    session = _session_with_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please touch a file"
        await pilot.press("enter")

        while len(app.screen_stack) < 2:
            await asyncio.sleep(0.01)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, PermissionAskScreen)

        option_list = screen.query_one(f"#{PERMISSION_ASK_OPTIONS_ID}", OptionList)
        option_list.highlighted = 5  # "Other..."
        await pilot.press("enter")
        await pilot.pause()

        other_input = screen.query_one(f"#{PERMISSION_ASK_INPUT_ID}", Input)
        other_input.value = "use /tmp instead"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "use /tmp instead" in str(tool_response.content)
        assert "Permission denied" in str(tool_response.content)


async def test_clear_gives_the_new_session_a_fresh_tool_registry() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "/clear"
        await pilot.press("enter")
        await pilot.pause()

        assert app._session.tool_registry is not None
        assert "ReadFile" in {tool.name() for tool in app._session.tool_registry.tools()}


async def test_clear_replaces_session_and_resets_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    process_config = ProcessConfig(session=SessionConfig(model="some/model"))
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        original_session_id = app._session.id

        prompt_input.text = "/clear"
        await pilot.press("enter")
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(history.children) == 0
        assert app._session.id != original_session_id
        assert app._session.config.model == "some/model"
        assert app._session.messages == []


async def test_clear_carries_over_thinking_settings_from_process_config() -> None:
    """Regression test: `/clear` used to hand-pick `model`/`interactive` onto the new
    `SessionConfig`, silently dropping `thinking_enabled`/`thinking_effort` back to their
    defaults. It now copies the full `ProcessConfig.session` template, which the thinking
    commands keep in sync, so a `/clear` after changing thinking settings preserves them.
    """
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.set_thinking_enabled(False)
        app.set_thinking_effort("low")

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "/clear"
        await pilot.press("enter")
        await pilot.pause()

        assert app._session.config.thinking_enabled is False
        assert app._session.config.thinking_effort == "low"


async def test_clear_does_not_disable_input_or_send_to_provider() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "/clear"
        await pilot.press("enter")
        await pilot.pause()

        assert prompt_input.disabled is False

    mock_provider.send_prompt.assert_not_called()


async def test_clear_rotates_log_file_when_session_log_enabled() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider), session_log_enabled=True)

    with patch("klorb.tui.repl.configure_logging") as mock_configure_logging:
        async with app.run_test() as pilot:
            prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
            prompt_input.text = "/clear"
            await pilot.press("enter")
            await pilot.pause()

    mock_configure_logging.assert_called_once_with(
        repl_mode=True, log_path=session_log_path(app._session.id))


async def test_clear_skips_log_rotation_when_session_log_disabled() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider), session_log_enabled=False)

    with patch("klorb.tui.repl.configure_logging") as mock_configure_logging:
        async with app.run_test() as pilot:
            prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
            prompt_input.text = "/clear"
            await pilot.press("enter")
            await pilot.pause()

    mock_configure_logging.assert_not_called()


async def test_streaming_response_updates_widget_progressively() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_chunk("Hel")
        on_chunk("lo")
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
        response_widgets = list(history.query(Markdown))

        assert len(response_widgets) == 1
        assert response_widgets[0].source == "Hello"


async def test_thinking_chunks_render_as_a_labeled_italicized_block_before_the_response() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
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
        assert thinking_widgets[0].content == "[italic]Let me think.[/italic]"
        assert len(response_widgets) == 1
        assert response_widgets[0].source == "Hello"


async def test_thinking_chunks_with_multiple_paragraphs_still_render_fully_italicized() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
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
        assert thinking_widgets[0].content == "[italic]First paragraph.\n\nSecond paragraph.[/italic]"


async def test_thinking_chunks_escape_literal_brackets() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
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

        assert thinking_widgets[0].content == r"[italic]check \[status][/italic]"
