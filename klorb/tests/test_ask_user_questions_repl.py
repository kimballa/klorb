# © Copyright 2026 Aaron Kimball
"""End-to-end tests: an `AskUserQuestions` tool call drives `AskUserQuestionsPanel` through
a real `ReplApp`, mirroring `test_tui_repl.py`'s `PermissionAskPanel` end-to-end tests."""

import asyncio
import json
from datetime import datetime
from typing import Callable
from unittest.mock import MagicMock

from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Input, Static

from klorb.api_provider import ProviderResponse
from klorb.message import Message, ToolCallRequest
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.registry import ToolRegistry
from klorb.tui import ReplApp
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID
from klorb.tui.panels.ask_user_questions_panel import ASK_USER_QUESTIONS_INPUT_ID, AskUserQuestionsPanel
from klorb.tui.widgets.prompt_input import PromptInput


async def _wait_until(pilot: Pilot[None], predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll `predicate` via repeated `pilot.pause()` calls until it's true.

    Mirrors `test_tui_repl.py`'s helper of the same name -- see that copy's docstring for why
    `pilot.pause()` (which drives Textual's message pump) beats a flat `asyncio.sleep()` here.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError(f"Timed out after {timeout}s waiting for condition")
        await pilot.pause()


def _reply(content: str = "final answer") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=5, processing_state="complete",
            timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=10,
    )


def _tool_call_reply(calls: list[tuple[str, str, str]]) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="", role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=id_, name=name, arguments=args) for id_, name, args in calls]),
        prompt_tokens=10,
    )


def _ask_call(id_: str, questions: list[dict]) -> tuple[str, str, str]:
    return id_, "AskUserQuestions", json.dumps({"questions": questions})


def _session_with_real_tools(provider: MagicMock, config: SessionConfig) -> Session:
    tool_registry = ToolRegistry(ProcessConfig(), config)
    return Session(config, provider=provider, tool_registry=tool_registry)


async def test_ask_user_questions_modal_appears_for_an_ask_tool_call() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_call("call_1", [{"header": "Auth", "question": "Which?", "options": []}])]),
        _reply(),
    ]
    config = SessionConfig(model="some/model")
    session = _session_with_real_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please ask me something"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(AskUserQuestionsPanel)))

        app.query_one(AskUserQuestionsPanel)
        assert app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).disabled


async def test_ask_user_questions_modal_answer_flows_back_into_the_tool_response() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_call("call_1", [{
            "header": "Auth", "question": "Which?",
            "options": [{"label": "JWT"}, {"label": "Cookie"}],
        }])]),
        _reply(),
    ]
    config = SessionConfig(model="some/model")
    session = _session_with_real_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please ask me something"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(AskUserQuestionsPanel)))
        app.query_one(AskUserQuestionsPanel)

        await pilot.press("enter")  # confirm the first ("JWT") row
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(AskUserQuestionsPanel)
        assert not app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).disabled
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "JWT" in str(tool_response.content)

        history_text = "\n".join(
            str(child.render()) for child in app.query_one(f"#{HISTORY_ID}", VerticalScroll).children
            if isinstance(child, Static))
        assert "Question 1 of 1 · Auth" in history_text
        assert "Which?" in history_text
        assert "Decision: JWT" in history_text


async def test_ask_user_questions_modal_escape_cancels_and_shows_error() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_call("call_1", [{"header": "Auth", "question": "Which?", "options": []}])]),
        _reply(),
    ]
    config = SessionConfig(model="some/model")
    session = _session_with_real_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please ask me something"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(AskUserQuestionsPanel)))
        app.query_one(AskUserQuestionsPanel)

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(AskUserQuestionsPanel)
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "declined" in str(tool_response.content)


async def test_ask_user_questions_modal_other_input_flows_back_as_free_text() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_call("call_1", [{
            "header": "Auth", "question": "Which?",
            "options": [{"label": "JWT"}, {"label": "Cookie"}],
        }])]),
        _reply(),
    ]
    config = SessionConfig(model="some/model")
    session = _session_with_real_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please ask me something"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(AskUserQuestionsPanel)))

        await pilot.press("o")
        await pilot.pause()
        input_widget = app.query_one(f"#{ASK_USER_QUESTIONS_INPUT_ID}", Input)
        input_widget.value = "neither, use SSO"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(AskUserQuestionsPanel)
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "neither, use SSO" in str(tool_response.content)
