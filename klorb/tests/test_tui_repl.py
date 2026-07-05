# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.repl."""

import asyncio
import json
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Literal
from unittest.mock import MagicMock
from unittest.mock import patch

import fixtures.sample_models as sample_models_package
import fixtures.sample_tools as sample_tools_package
import pytest
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Input
from textual.widgets import Markdown
from textual.widgets import OptionList
from textual.widgets import Static

from klorb import process_config as process_config_module
from klorb.api_provider import ProviderResponse
from klorb.api_provider import ResponseAborted
from klorb.logging_config import session_log_path
from klorb.message import Message
from klorb.message import ToolCallRequest
from klorb.models.registry import ModelRegistry
from klorb.permissions.directory_access import DirRules
from klorb.process_config import CONFIG_SCHEMA_NAME
from klorb.process_config import SESSION_DEFAULTS_KEY
from klorb.process_config import ProcessConfig
from klorb.process_config import project_config_path
from klorb.schema_envelope import read_versioned_json
from klorb.session import PermissionAskContext
from klorb.session import PermissionDecision
from klorb.session import Session
from klorb.session import SessionConfig
from klorb.tools.registry import ToolRegistry
from klorb.tui.confirm_screen import CONFIRM_NO_ID
from klorb.tui.confirm_screen import CONFIRM_YES_ID
from klorb.tui.confirm_screen import ConfirmScreen
from klorb.tui.palette import PALETTE_PREFIX
from klorb.tui.palette import PROMPT_PALETTE_ID
from klorb.tui.palette import PaletteOption
from klorb.tui.palette import PromptPalette
from klorb.tui.permission_ask_screen import PERMISSION_ASK_INPUT_ID
from klorb.tui.permission_ask_screen import PERMISSION_ASK_OPTIONS_ID
from klorb.tui.permission_ask_screen import PermissionAskScreen
from klorb.tui.repl import CONFIG_MISSING_MESSAGE
from klorb.tui.repl import HISTORY_ID
from klorb.tui.repl import PALETTE_HINT_ID
from klorb.tui.repl import PALETTE_HINT_TEXT
from klorb.tui.repl import PROMPT_INPUT_ID
from klorb.tui.repl import STATUS_BAR_ID
from klorb.tui.repl import THINKING_LABEL
from klorb.tui.repl import TOOL_USE_LABEL
from klorb.tui.repl import PromptInput
from klorb.tui.repl import ReplApp
from klorb.tui.repl import ToolCallLimitScreen
from klorb.tui.repl import ToolCallStatic
from klorb.tui.repl import format_token_count
from klorb.tui.trust_commands import TRUST_WORKSPACE_LABEL
from klorb.workspace import TrustManager
from klorb.workspace import Workspace


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


def _palette_hit_texts(palette: PromptPalette) -> set[str]:
    """The canonical `text` of every hit currently rendered in `palette`'s rows."""
    options = palette._options
    assert all(isinstance(option, PaletteOption) for option in options)
    return {str(option.hit.text) for option in options if isinstance(option, PaletteOption)}


@pytest.fixture(autouse=True)
def _user_config_present(tmp_path: Path) -> Iterator[None]:
    """Make `user_config_path()` resolve to an existing file by default, so `ReplApp.on_mount`'s
    "config file not found" notice (see `CONFIG_MISSING_MESSAGE`) doesn't leak an extra
    `Static` into every other test's history assertions. The tests that specifically exercise
    the notice re-patch `klorb.tui.repl.user_config_path` themselves, overriding this.
    """
    config_path = tmp_path / "klorb-config.json"
    config_path.write_text("{}", encoding="utf-8")
    with patch("klorb.tui.repl.user_config_path", return_value=config_path):
        yield


@pytest.fixture
def _isolate_process_config_layers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the `/etc`- and per-user-scope `klorb-config.json` layers `load_process_config()`
    reads at empty locations under `tmp_path`, and blank out the packaged built-in-defaults
    layer -- for the workspace-bootstrap/"Trust workspace" tests below, whose
    `ReplApp._apply_workspace_config` calls the real `load_process_config()` (unlike every
    other test in this file, which never reaches it). Mirrors
    `test_process_config.py`'s `_isolate_config_layers` fixture.
    """
    monkeypatch.setenv(
        process_config_module.KLORB_ETC_CONFIG_ENV_VAR, str(tmp_path / "etc" / "klorb-config.json"))
    monkeypatch.setattr(process_config_module, "KLORB_CONFIG_DIR", tmp_path / "user-config")
    monkeypatch.setattr(process_config_module, "_default_config_layer", lambda: {})


async def _invoke_clear_session(pilot: Pilot[None]) -> None:
    """Type `>clear` and press enter to select "Clear session" from the inline palette
    (see docs/specs/command-palette-from-prompt.md), mirroring how a real user reaches it now
    that the bare `/clear` prompt text is no longer special-cased.
    """
    await pilot.press(*f"{PALETTE_PREFIX}clear")
    await pilot.press("enter")
    await pilot.pause()


async def _wait_until(pilot: Pilot[None], predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll `predicate` via repeated `pilot.pause()` calls until it's true.

    Some scroll effects (e.g. `VerticalScroll.scroll_end()`/`scroll_home()` with the default
    `immediate=False`) are deferred until after a layout refresh rather than applying
    synchronously, so a single `pilot.pause()` isn't reliably enough to observe their result.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"Timed out after {timeout}s waiting for condition")
        await pilot.pause()


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


async def test_shows_config_missing_notice_when_user_config_file_absent(tmp_path: Path) -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    with patch("klorb.tui.repl.user_config_path", return_value=tmp_path / "does-not-exist.json"):
        async with app.run_test():
            history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
            notice = history.query_one(".notice", Static)
            assert str(notice.content) == CONFIG_MISSING_MESSAGE


async def test_omits_config_missing_notice_when_user_config_file_present(tmp_path: Path) -> None:
    config_path = tmp_path / "klorb-config.json"
    config_path.write_text("{}", encoding="utf-8")
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    with patch("klorb.tui.repl.user_config_path", return_value=config_path):
        async with app.run_test():
            history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
            assert len(history.query(".notice")) == 0


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
        prompt_widget = history.query(Static).exclude(".mascot").first()
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
        assert len(history.query(Static).exclude(".mascot")) == 0

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


async def test_aborted_response_keeps_prompt_in_history_and_clears_input() -> None:
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
        history.query_one(".prompt", Static)
        history.query_one(".interrupted", Static)
        assert prompt_input.text == ""
        assert prompt_input.disabled is False

    # The user turn stays in history, tagged "aborted" rather than discarded — nothing ever
    # streamed in before the abort, so there's no assistant/thinking placeholder alongside it.
    assert [m.role for m in session.messages] == ["system", "user"]
    assert session.messages[-1].processing_state == "aborted"


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
        history.query_one(".prompt", Static)
        history.query_one(".interrupted", Static)
        assert prompt_input.text == ""
        assert prompt_input.disabled is False
        assert app.check_action("abort_response", ()) is False

    # The user turn stays in history, tagged "aborted", rather than being discarded.
    assert [m.role for m in session.messages] == ["system", "user"]
    assert session.messages[-1].processing_state == "aborted"


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
        prompt_widget = history.query(Static).exclude(".mascot").first()
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


async def test_aborting_a_turn_keeps_its_completed_tool_call_widgets() -> None:
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

        # The echo call already ran, with a real (if trivial) side effect, before the second
        # round's abort — it stays in the transcript exactly like a completed turn's would.
        assert len(list(history.query(ToolCallStatic))) == 1
        history.query_one(".interrupted", Static)

    assert [m.role for m in session.messages] == [
        "system", "tool_defs", "user", "tool_use", "tool_response"]
    assert session.messages[2].processing_state == "aborted"
    assert len(app._tool_call_widgets) == 1


async def test_each_tool_call_round_gets_its_own_thinking_and_response_blocks() -> None:
    mock_provider = MagicMock()
    calls_made = 0

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        nonlocal calls_made
        calls_made += 1
        if calls_made == 1:
            on_thinking_chunk("Round one thinking.")
            on_chunk("Round one reply.")
            return _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
        on_thinking_chunk("Round two thinking.")
        on_chunk("Round two reply.")
        return _reply("Round two reply.")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        thinking_bodies = list(history.query(".thinking-body").results(Static))
        response_widgets = list(history.query(Markdown))

        # Each round gets its own thinking/response widgets, rather than one growing block
        # absorbing both rounds' text across the tool call in between.
        assert [w.content for w in thinking_bodies] == [
            "[italic]Round one thinking.[/italic]", "[italic]Round two thinking.[/italic]"]
        assert [w.source for w in response_widgets] == ["Round one reply.", "Round two reply."]

        # The tool call sits between the two rounds' blocks in the transcript, matching the
        # actual time-order: round one drafts, then the tool call runs, then round two drafts.
        children = list(history.children)
        tool_call_index = children.index(history.query_one(ToolCallStatic))
        assert children.index(response_widgets[0]) < tool_call_index < children.index(response_widgets[1])


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
        widgets = list(history.query(Static).exclude(".mascot"))
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
        output_widget = list(history.query(Static).exclude(".mascot"))[1]
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
        output_widget = list(history.query(Static).exclude(".mascot"))[1]
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
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
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
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
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
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    process_config = ProcessConfig(
        session=SessionConfig(model="some/model", workspace=Workspace(path=tmp_path)))
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
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
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
        await _invoke_clear_session(pilot)

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

        await _invoke_clear_session(pilot)

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

        await _invoke_clear_session(pilot)

        assert app._session.config.thinking_enabled is False
        assert app._session.config.thinking_effort == "low"


async def test_clear_does_not_disable_input_or_send_to_provider() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        await _invoke_clear_session(pilot)

        assert prompt_input.disabled is False

    mock_provider.send_prompt.assert_not_called()


async def test_clear_rotates_log_file_when_session_log_enabled() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider), session_log_enabled=True)

    with patch("klorb.tui.repl.configure_logging") as mock_configure_logging:
        async with app.run_test() as pilot:
            await _invoke_clear_session(pilot)

    mock_configure_logging.assert_called_once_with(
        repl_mode=True, log_path=session_log_path(app._session.id))


async def test_clear_skips_log_rotation_when_session_log_disabled() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider), session_log_enabled=False)

    with patch("klorb.tui.repl.configure_logging") as mock_configure_logging:
        async with app.run_test() as pilot:
            await _invoke_clear_session(pilot)

    mock_configure_logging.assert_not_called()


# --- input history (up/down-arrow recall) ---


async def _complete_turn(pilot: Pilot[None], app: ReplApp) -> None:
    """Wait for the in-flight model turn to finish and the input box to re-enable."""
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_up_arrow_recalls_the_most_recent_prompt() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        assert prompt_input.text == ""
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "first"


async def test_repeated_up_arrow_walks_back_through_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = "second"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "second"

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "first"


async def test_down_arrow_walks_forward_and_returns_to_empty_draft() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = "second"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "second"

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "first"

        await pilot.press("down")
        await pilot.pause()
        assert prompt_input.text == "second"

        await pilot.press("down")
        await pilot.pause()
        assert prompt_input.text == ""


async def test_down_arrow_past_most_recent_restores_the_in_progress_draft() -> None:
    """Walking up from an untouched draft and back down past the most recent entry restores
    that draft rather than clearing the box to empty."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = "an unsent draft"
        await pilot.press("home")
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "first"

        await pilot.press("down")
        await pilot.pause()
        assert prompt_input.text == "an unsent draft"


async def test_editing_a_recalled_prompt_detaches_and_resets_recall_position() -> None:
    """After typing into a recalled entry, the next up-arrow starts fresh from the most
    recent entry rather than continuing from the now-stale recall position."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        prompt_input.text = "second"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        # Walk back to the oldest entry, then edit it.
        await pilot.press("up", "up")
        await pilot.pause()
        assert prompt_input.text == "first"
        await pilot.press("x")
        await pilot.pause()
        assert prompt_input.text == "firstx"

        # Move to the start so the next up-arrow triggers recall rather than cursor movement.
        await pilot.press("home")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        # Detached, so recall jumps to the most recent entry ("second"), not continuing from
        # the stale position that would have gone past "first" with nowhere to go.
        assert prompt_input.text == "second"


async def test_empty_prompt_is_not_recorded_in_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        # An empty submit doesn't get recorded, so there's still only one history entry.
        prompt_input.text = "   "
        await pilot.press("enter")
        await pilot.pause()
        assert prompt_input.text == "   "

        await pilot.press("home", "up")
        await pilot.pause()
        assert prompt_input.text == "first"


async def test_clear_resets_input_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")
        await _complete_turn(pilot, app)

        await _invoke_clear_session(pilot)

        # Clearing the session drops "first" from the input history, but the
        # ">Clear session" selection that triggered it is recorded afterward (see
        # `ReplApp._run_palette_command`), so it's the sole entry left to recall — not
        # "first", and no entry further back than it either.
        assert prompt_input.text == ""
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == f"{PALETTE_PREFIX}Clear session"
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == f"{PALETTE_PREFIX}Clear session"


# --- command palette from the prompt (a leading ">" in the prompt input) ---


async def test_bare_gt_shows_the_full_discovery_list() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(">")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is True
        assert palette.option_count > 0
        assert "Clear session" in _palette_hit_texts(palette)


async def test_bare_gt_lists_rows_alphabetically_since_every_score_ties_at_zero() -> None:
    """A `DiscoveryHit` (what `discover()` yields for the empty query behind a bare `>`)
    always scores `0.0`, so with nothing to rank by, the alphabetical tiebreak alone decides
    the full listing's order (see `gather_palette_hits`).
    """
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(">")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        options = palette._options
        assert all(isinstance(option, PaletteOption) for option in options)
        texts = [str(option.hit.text) for option in options if isinstance(option, PaletteOption)]
        assert texts == sorted(texts, key=str.casefold)


async def test_typing_a_query_narrows_the_palette_to_matching_hits() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}clear")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is True
        assert _palette_hit_texts(palette) == {"Clear session"}


async def test_up_down_arrows_move_the_palette_highlight_not_the_cursor() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(">")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        first_highlight = palette.highlighted
        assert first_highlight is not None

        await pilot.press("down")
        await pilot.pause()
        assert palette.highlighted == first_highlight + 1

        await pilot.press("up")
        await pilot.pause()
        assert palette.highlighted == first_highlight

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        assert prompt_input.text == ">"


async def test_enter_executes_the_highlighted_palette_command_and_clears_the_input() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}clear")
        await pilot.press("enter")
        await pilot.pause()

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert prompt_input.text == ""
        assert palette.display is False
        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(history.children) == 0  # clear_session() ran, not a submitted prompt.


async def test_palette_selection_is_recorded_in_history_by_its_canonical_name() -> None:
    """Recalling a palette selection via up-arrow should show `>Clear session` (the
    canonical/standard name), not whatever partial query the user actually typed.
    """
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}cle")
        await pilot.press("enter")
        await pilot.pause()

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == f"{PALETTE_PREFIX}Clear session"


async def test_recalling_a_past_palette_selection_does_not_resurface_the_popup() -> None:
    """Browsing history up to a recalled `>Clear session` entry (per
    docs/specs/command-palette-from-prompt.md's "History browsing" section) shows it as
    plain recalled text; the popup only reappears once the user actually edits it.
    """
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}clear")
        await pilot.press("enter")
        await pilot.pause()

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        await pilot.press("up")
        await pilot.pause()

        assert prompt_input.text == f"{PALETTE_PREFIX}Clear session"
        assert palette.display is False


async def test_escape_dismisses_the_palette_and_continues_as_plain_text() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}clear")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is True

        await pilot.press("escape")
        await pilot.pause()
        assert palette.display is False

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)  # type: ignore[unreachable]
        await pilot.press("!")
        await pilot.pause()
        assert prompt_input.text == f"{PALETTE_PREFIX}clear!"
        assert palette.display is False


async def test_enter_with_no_matching_palette_option_submits_as_a_plain_prompt() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        gibberish = f"{PALETTE_PREFIX}asd3434j2asdadkjfkjl34kj"
        await pilot.press(*gibberish)
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is False

        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = history.query(Static).exclude(".mascot").first()
        assert prompt_widget.content == gibberish


async def test_no_match_then_space_dismisses_the_palette() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        await pilot.press(*f"{PALETTE_PREFIX}zzzznomatch")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is False

        await pilot.press("space")
        await pilot.press(*"more text")
        await pilot.pause()

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        assert prompt_input.text == f"{PALETTE_PREFIX}zzzznomatch more text"
        assert palette.display is False


async def test_backspacing_the_leading_gt_to_empty_closes_the_palette() -> None:
    """Regression test: backspace is dispatched via a bound `TextArea` action
    (`action_delete_left`) that Textual applies *after* `_on_key` returns, not from within
    it — so `_refresh_palette` can't reliably read the post-edit text if called directly from
    `_on_key`. It's driven from `on_text_area_changed` instead (see that method's docstring),
    since `TextArea.Changed` only fires once the edit has actually landed.
    """
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        await pilot.press(">")
        await pilot.pause()

        palette = app.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)
        assert palette.display is True

        await pilot.press("backspace")
        await pilot.pause()

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        assert prompt_input.text == ""
        assert palette.display is False
        assert palette.option_count == 0  # type: ignore[unreachable]

        # Up/down should now drive ordinary history recall again, not a (closed) popup.
        prompt_input.text = "earlier prompt"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press(">")
        await pilot.press("backspace")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        assert prompt_input.text == "earlier prompt"


async def test_palette_hint_shown_only_while_the_box_is_empty_or_bare_gt() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        hint = app.query_one(f"#{PALETTE_HINT_ID}", Static)
        assert str(hint.render()).strip() == PALETTE_HINT_TEXT

        await pilot.press(">")
        await pilot.pause()
        assert str(hint.render()).strip() == PALETTE_HINT_TEXT

        await pilot.press("x")
        await pilot.pause()
        assert str(hint.render()) == ""


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


async def test_streaming_updates_stay_pinned_to_the_bottom_when_the_user_is_at_the_bottom() -> None:
    """`ReplApp._scroll_if_pinned` is where the app decides whether to follow new streaming
    content to the bottom; spying on it (rather than asserting on `history.scroll_y` /
    `max_scroll_y` directly) tests that decision without depending on exactly when Textual's
    own deferred `scroll_end()` settles the viewport, which is unrelated to the bug this
    covers and isn't something this test needs to pin down.
    """
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_thinking_chunk("Thinking...")
        on_chunk("Hello")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        with patch.object(ReplApp, "_scroll_if_pinned", autospec=True) as mock_scroll_if_pinned:
            prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
            prompt_input.text = "hi"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

        assert mock_scroll_if_pinned.call_args_list
        was_pinned_values = [call.args[-1] for call in mock_scroll_if_pinned.call_args_list]
        assert all(was_pinned_values)


async def test_streaming_updates_do_not_yank_the_scroll_when_the_user_has_scrolled_away() -> None:
    """See `test_streaming_updates_stay_pinned_to_the_bottom_when_the_user_is_at_the_bottom` for
    why this spies on `_scroll_if_pinned` rather than asserting on the viewport's actual scroll
    position.
    """
    mock_provider = MagicMock()
    first_chunk_sent = threading.Event()
    release_rest_of_turn = threading.Event()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_thinking_chunk("First bit of thinking.")
        first_chunk_sent.set()
        release_rest_of_turn.wait(timeout=5)
        on_thinking_chunk(" More thinking, streamed in after the user scrolled away.")
        on_chunk("Hello")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test(size=(40, 6)) as pilot:
        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        for i in range(20):
            history.mount(Static(f"padding line {i}"))
        await pilot.pause()

        with patch.object(ReplApp, "_scroll_if_pinned", autospec=True) as mock_scroll_if_pinned:
            prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
            prompt_input.text = "hi"
            await pilot.press("enter")

            while not first_chunk_sent.is_set():
                await asyncio.sleep(0.01)
            await pilot.pause()

            # The user scrolls up to reread earlier output while the rest of the turn streams in.
            history.scroll_home(animate=False)
            await _wait_until(pilot, lambda: not history.is_vertical_scroll_end)

            release_rest_of_turn.set()
            await app.workers.wait_for_complete()
            await pilot.pause()

        was_pinned_values = [call.args[-1] for call in mock_scroll_if_pinned.call_args_list]
        assert was_pinned_values
        assert not any(was_pinned_values[was_pinned_values.index(False):])


# --- workspace trust: bootstrap at startup and the "Trust workspace" command ---


def _process_config_for_workspace(workspace: Workspace, model: str = "some/model") -> ProcessConfig:
    return ProcessConfig(session=SessionConfig(model=model, workspace=workspace))


def _repl_app_for_workspace(
    workspace: Workspace, trust_manager: TrustManager | None, model: str = "some/model",
) -> ReplApp:
    process_config = _process_config_for_workspace(workspace, model)
    session = Session(
        process_config.session.model_copy(), provider=MagicMock(), session_id=TEST_SESSION_ID,
        process_config=process_config)
    return ReplApp(session=session, process_config=process_config, trust_manager=trust_manager)


def _notice_texts(app: ReplApp) -> list[str]:
    history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
    notices = history.query(".notice")
    assert all(isinstance(widget, Static) for widget in notices)
    return [str(widget.content) for widget in notices if isinstance(widget, Static)]


async def test_registered_trusted_workspace_announces_and_shows_no_modal(tmp_path: Path) -> None:
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert f"Working in project: {tmp_path}" in _notice_texts(app)


async def test_registered_untrusted_workspace_announces_not_trusted_with_no_modal(tmp_path: Path) -> None:
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1
        notices = _notice_texts(app)
        assert any(
            f"The workspace at {tmp_path} is not trusted" in notice and TRUST_WORKSPACE_LABEL in notice
            for notice in notices)


async def test_no_trust_manager_skips_bootstrap_and_announcement_entirely(tmp_path: Path) -> None:
    workspace = Workspace(path=tmp_path)
    app = _repl_app_for_workspace(workspace, trust_manager=None)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert _notice_texts(app) == []


@pytest.mark.usefixtures("_isolate_process_config_layers")
async def test_bootstrap_open_as_project_and_trust_registers_and_writes_config(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=project_root)
    app = _repl_app_for_workspace(workspace, trust_manager, model="burned/model")

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # "Open as project?" -> Yes
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # "Do you trust...?" -> Yes
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert f"Working in project: {project_root}" in _notice_texts(app)
        assert app._session.config.workspace.trusted is True

    resolved = trust_manager.resolve_workspace(project_root)
    assert resolved.is_project is True
    assert resolved.trusted is True

    raw = read_versioned_json(project_config_path(project_root), expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = raw[SESSION_DEFAULTS_KEY]
    assert session_defaults["model"] == "burned/model"
    assert session_defaults["readDirs"]["allow"] == [str(project_root)]
    assert session_defaults["writeDirs"]["allow"] == [str(project_root)]


@pytest.mark.usefixtures("_isolate_process_config_layers")
async def test_bootstrap_declining_project_but_trusting_keeps_it_in_memory_only(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=project_root)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_NO_ID}")  # "Open as project?" -> No
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # "Do you trust...?" -> Yes
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.id is None
        assert app._session.config.workspace.is_project is False
        assert app._session.config.workspace.trusted is True

    assert trust_manager.resolve_workspace(project_root).id is None
    assert not project_config_path(project_root).is_file()


@pytest.mark.usefixtures("_isolate_process_config_layers")
async def test_bootstrap_declining_both_stays_unregistered_and_untrusted(
    tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=tmp_path)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_NO_ID}")  # "Open as project?" -> No
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_NO_ID}")  # "Do you trust...?" -> No
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.trusted is False
        assert any("not trusted" in notice for notice in _notice_texts(app))

    assert trust_manager.resolve_workspace(tmp_path).id is None
    assert not project_config_path(tmp_path).is_file()


@pytest.mark.usefixtures("_isolate_process_config_layers")
async def test_trust_workspace_command_persists_and_offers_config_init(
    tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1  # already registered: no bootstrap modal at mount

        await pilot.press(*f"{PALETTE_PREFIX}trust")
        await pilot.press("enter")
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # confirm trust
        await pilot.pause()

        # tmp_path has no config file yet and is a registered project, so a second modal offers
        # to write one from the live session's current settings.
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # yes, initialize
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.trusted is True
        assert any("Trusted workspace" in notice for notice in _notice_texts(app))

    assert trust_manager.resolve_workspace(tmp_path).trusted is True
    assert project_config_path(tmp_path).is_file()


@pytest.mark.usefixtures("_isolate_process_config_layers")
async def test_trust_workspace_command_declining_config_init_leaves_no_file(
    tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()

        await pilot.press(*f"{PALETTE_PREFIX}trust")
        await pilot.press("enter")
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # confirm trust
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_NO_ID}")  # decline config init
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.trusted is True

    assert not project_config_path(tmp_path).is_file()


@pytest.mark.usefixtures("_isolate_process_config_layers")
async def test_trust_workspace_for_non_project_workspace_skips_config_init_prompt(
    tmp_path: Path,
) -> None:
    """A non-project (never registered) workspace can only reach "already resolved, untrusted"
    mid-session -- a fresh mount always treats `workspace.id is None` as "never resolved" and
    runs the full bootstrap (see the tests above). Simulated here by constructing the app with
    no `TrustManager` (so mounting doesn't itself trigger a fresh bootstrap) and attaching one
    afterward, then driving `trust_workspace()` directly as a background task rather than via
    the palette, so this test can focus purely on the "not a project" branch.
    """
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=tmp_path, is_project=False, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager=None)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._trust_manager = trust_manager

        app.trust_workspace()
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # confirm trust
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.trusted is True

    assert not project_config_path(tmp_path).is_file()
