# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.repl."""

from unittest.mock import MagicMock

from textual.containers import VerticalScroll
from textual.widgets import Input
from textual.widgets import Markdown
from textual.widgets import Static

from klorb.session import Session
from klorb.session import SessionConfig
from klorb.tui.repl import HISTORY_ID
from klorb.tui.repl import PROMPT_INPUT_ID
from klorb.tui.repl import ReplApp


def _session(provider: MagicMock, model: str = "some/model") -> Session:
    return Session(SessionConfig(model=model), provider=provider)


async def test_submitting_a_prompt_shows_it_and_the_response() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "model reply"
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", Input)
        prompt_input.value = "what is 2+2?"
        await prompt_input.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = history.query_one(Static)
        response_widget = history.query_one(Markdown)

        assert prompt_widget.content == "what is 2+2?"
        assert response_widget.source == "model reply"
        assert prompt_input.value == ""

    mock_provider.send_prompt.assert_called_once_with("what is 2+2?", model="some/model")


async def test_submitting_an_empty_prompt_does_nothing() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", Input)
        prompt_input.value = "   "
        await prompt_input.action_submit()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(history.children) == 0

    mock_provider.send_prompt.assert_not_called()


async def test_provider_error_is_shown_in_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = RuntimeError("boom")
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", Input)
        prompt_input.value = "hi"
        await prompt_input.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        error_widget = history.query_one(".error", Static)

        assert "boom" in str(error_widget.content)
        assert prompt_input.disabled is False


async def test_select_model_updates_active_model_and_subtitle() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "ok"
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.select_model("other/model")
        await pilot.pause()
        assert app.sub_title == "other/model"

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", Input)
        prompt_input.value = "hi"
        await prompt_input.action_submit()
        await app.workers.wait_for_complete()
        await pilot.pause()

    mock_provider.send_prompt.assert_called_once_with("hi", model="other/model")


async def test_initial_message_is_submitted_as_first_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = "model reply"
    app = ReplApp(session=_session(mock_provider), initial_message="what is 2+2?")

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = history.query_one(Static)
        response_widget = history.query_one(Markdown)

        assert prompt_widget.content == "what is 2+2?"
        assert response_widget.source == "model reply"

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", Input)
        assert prompt_input.disabled is False

    mock_provider.send_prompt.assert_called_once_with("what is 2+2?", model="some/model")
