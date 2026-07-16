# © Copyright 2026 Aaron Kimball
"""End-to-end tests: an `EscalatePrivileges` tool call drives `EscalatePrivilegesPanel` through
a real `ReplApp`, mirroring `test_ask_user_questions_repl.py`."""

import asyncio
import json
from datetime import datetime
from typing import Callable
from unittest.mock import MagicMock

import pytest
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Static

from klorb.api_provider import ProviderResponse
from klorb.message import Message, ToolCallRequest
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.registry import ToolRegistry
from klorb.tui.panels.escalate_privileges_panel import EscalatePrivilegesPanel
from klorb.tui.repl import HISTORY_ID, PROMPT_INPUT_ID, PromptInput, ReplApp


@pytest.fixture(autouse=True)
def _stub_estimate_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid tiktoken's network fetch by returning a constant token estimate for any text."""
    monkeypatch.setattr("klorb.session.estimate_tokens", lambda _text: 1)


async def _wait_until(pilot: Pilot[None], predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll `predicate` via repeated `pilot.pause()` calls until it's true."""
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


def _escalate_call(id_: str, scope: str = "workspace") -> tuple[str, str, str]:
    return id_, "EscalatePrivileges", json.dumps({"scope": scope})


def _session_with_real_tools(provider: MagicMock, config: SessionConfig) -> tuple[Session, ProcessConfig]:
    process_config = ProcessConfig()
    tool_registry = ToolRegistry(process_config, config)
    session = Session(config, provider=provider, tool_registry=tool_registry, process_config=process_config)
    return session, process_config


async def test_escalate_privileges_panel_appears_for_an_escalate_tool_call() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1")]),
        _reply(),
    ]
    config = SessionConfig(model="some/model")
    session, _ = _session_with_real_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please escalate"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(EscalatePrivilegesPanel)))

        app.query_one(EscalatePrivilegesPanel)
        assert app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).disabled


async def test_escalate_privileges_approve_records_scope_and_flows_back() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1")]),
        _reply(),
    ]
    config = SessionConfig(model="some/model")
    session, process_config = _session_with_real_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please escalate"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(EscalatePrivilegesPanel)))
        app.query_one(EscalatePrivilegesPanel)

        await pilot.press("enter")  # confirm the highlighted "Approve" row
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(EscalatePrivilegesPanel)
        assert not app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).disabled
        assert "workspace" in app._session.config.approved_scopes
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "approved" in str(tool_response.content)

        history_text = "\n".join(
            str(child.render()) for child in app.query_one(f"#{HISTORY_ID}", VerticalScroll).children
            if isinstance(child, Static))
        assert "Privilege escalation requested" in history_text
        assert ".klorb" in history_text
        assert "Decision: Approve" in history_text


async def test_escalate_privileges_deny_keeps_scope_locked_and_reports_denial() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1")]),
        _reply(),
    ]
    config = SessionConfig(model="some/model")
    session, process_config = _session_with_real_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please escalate"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(EscalatePrivilegesPanel)))

        await pilot.press("down")  # move to "Deny"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(EscalatePrivilegesPanel)
        assert "workspace" not in app._session.config.approved_scopes
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "denied" in str(tool_response.content)


async def test_escalate_privileges_escape_denies() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1")]),
        _reply(),
    ]
    config = SessionConfig(model="some/model")
    session, process_config = _session_with_real_tools(mock_provider, config)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please escalate"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: bool(app.query(EscalatePrivilegesPanel)))

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert not app.query(EscalatePrivilegesPanel)
        assert "workspace" not in app._session.config.approved_scopes
        tool_response = next(m for m in app._session.messages if m.role == "tool_response")
        assert "denied" in str(tool_response.content)
