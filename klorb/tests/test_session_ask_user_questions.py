# © Copyright 2026 Aaron Kimball
"""Tests for Session._resolve_ask_user_questions / the AskUserQuestionsRequired branch of
Session._run_tool_calls."""

import json
from datetime import datetime
from unittest.mock import MagicMock

from klorb.api_provider import ProviderResponse
from klorb.message import Message, ToolCallRequest
from klorb.process_config import ProcessConfig
from klorb.session import (
    AskUserQuestionsAnswer,
    AskUserQuestionsItemContext,
    Session,
    SessionConfig,
    TurnEventHandlers,
)
from klorb.tools.registry import ToolRegistry


def _reply(content: str = "done") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=5, processing_state="complete",
            timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=10,
    )


def _ask_call(id_: str, questions: list[dict]) -> tuple[str, str, str]:
    return id_, "AskUserQuestions", json.dumps({"questions": questions})


def _question(header: str = "Auth", options: list[dict] | None = None) -> dict:
    return {
        "header": header,
        "question": f"Which {header.lower()}?",
        "options": [] if options is None else options,
    }


def _tool_call_reply(calls: list[tuple[str, str, str]]) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="", role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=id_, name=name, arguments=args) for id_, name, args in calls]),
        prompt_tokens=10,
    )


def _session(mock_provider: MagicMock) -> Session:
    config = SessionConfig(model="some/model")
    tool_registry = ToolRegistry.discover_tools(ProcessConfig(), config)
    return Session(config, provider=mock_provider, tool_registry=tool_registry)


def _tool_response_content(session: Session) -> str:
    tool_response = next(m for m in session.messages if m.role == "tool_response")
    assert isinstance(tool_response.content, str)
    return tool_response.content


def test_no_callback_fails_closed() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_call("call_1", [_question()])]),
        _reply(),
    ]
    session = _session(mock_provider)

    response = session.send_turn("try it")

    assert response == "done"
    assert "Error:" in _tool_response_content(session)
    assert "1 question" in _tool_response_content(session)


def test_single_question_answered_normally() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_call("call_1", [_question(header="Auth")])]),
        _reply(),
    ]
    session = _session(mock_provider)
    on_ask = MagicMock(return_value=AskUserQuestionsAnswer(answer="JWT"))

    response = session.send_turn("try it", TurnEventHandlers(on_ask_user_questions=on_ask))

    assert response == "done"
    content = _tool_response_content(session)
    assert "JWT" in content
    assert "Auth" in content
    on_ask.assert_called_once()
    (ctx,), _ = on_ask.call_args
    assert isinstance(ctx, AskUserQuestionsItemContext)
    assert ctx.header == "Auth"
    assert ctx.index == 0
    assert ctx.total == 1


def test_multi_question_cancel_short_circuits_and_echoes_prior_answers() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_call("call_1", [
            _question(header="Auth"), _question(header="DB"), _question(header="Cache"),
        ])]),
        _reply(),
    ]
    session = _session(mock_provider)
    on_ask = MagicMock(side_effect=[
        AskUserQuestionsAnswer(answer="JWT"),
        AskUserQuestionsAnswer(cancelled=True),
    ])

    response = session.send_turn("try it", TurnEventHandlers(on_ask_user_questions=on_ask))

    assert response == "done"
    content = _tool_response_content(session)
    assert "DB" in content  # names the cancelled question
    assert "Auth" in content  # echoes the already-collected answer
    assert on_ask.call_count == 2  # "Cache" is never asked about


def test_recommended_option_is_forwarded_unmodified_and_not_auto_selected() -> None:
    mock_provider = MagicMock()
    options: list[dict] = [
        {"label": "JWT", "description": "Stateless", "recommended": True},
        {"label": "Cookie"},
    ]
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_call("call_1", [_question(options=options)])]),
        _reply(),
    ]
    session = _session(mock_provider)
    on_ask = MagicMock(return_value=AskUserQuestionsAnswer(answer="Cookie"))

    session.send_turn("try it", TurnEventHandlers(on_ask_user_questions=on_ask))

    (ctx,), _ = on_ask.call_args
    assert [(o.label, o.recommended) for o in ctx.options] == [("JWT", True), ("Cookie", False)]
    # Session forwards the callback's answer verbatim -- it never substitutes the
    # recommended option on the caller's behalf.
    assert "Cookie" in _tool_response_content(session)
