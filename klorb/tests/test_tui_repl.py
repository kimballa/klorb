# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.repl."""

from datetime import datetime
from unittest.mock import MagicMock
from unittest.mock import patch

import fixtures.sample_models as sample_models_package
from textual.containers import VerticalScroll
from textual.widgets import Markdown
from textual.widgets import Static

from klorb.api_provider import ProviderResponse
from klorb.logging_config import session_log_path
from klorb.message import Message
from klorb.models.registry import ModelRegistry
from klorb.session import Session
from klorb.session import SessionConfig
from klorb.tui.repl import HISTORY_ID
from klorb.tui.repl import PROMPT_INPUT_ID
from klorb.tui.repl import STATUS_BAR_ID
from klorb.tui.repl import THINKING_LABEL
from klorb.tui.repl import PromptInput
from klorb.tui.repl import ReplApp
from klorb.tui.repl import format_token_count


TEST_SESSION_ID = "test-session-id"


def _session(provider: MagicMock, model: str = "some/model") -> Session:
    return Session(SessionConfig(model=model), provider=provider, session_id=TEST_SESSION_ID)


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


async def test_clear_replaces_session_and_resets_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

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
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, on_chunk=None,
        on_thinking_chunk=None,
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
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, on_chunk=None,
        on_thinking_chunk=None,
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
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, on_chunk=None,
        on_thinking_chunk=None,
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
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, on_chunk=None,
        on_thinking_chunk=None,
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
