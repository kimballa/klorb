# © Copyright 2026 Aaron Kimball
"""Tests for Session._resolve_escalate_privileges / the EscalatePrivilegesRequired branch of
Session._run_tool_calls."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.api_provider import ProviderResponse
from klorb.message import Message, ToolCallRequest
from klorb.paths import KLORB_DATA_DIR, KLORB_STATE_DIR
from klorb.process_config import ProcessConfig
from klorb.session import (
    EscalatePrivilegesContext,
    EscalatePrivilegesDecision,
    Session,
    SessionConfig,
    TurnEventHandlers,
)
from klorb.tools.registry import ToolRegistry
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _stub_estimate_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid tiktoken's network fetch by returning a constant token estimate for any text."""
    monkeypatch.setattr("klorb.session.mixins.prompt_setup.estimate_tokens", lambda _text: 1)
    monkeypatch.setattr("klorb.session.mixins.tool_execution.estimate_tokens", lambda _text: 1)
    monkeypatch.setattr("klorb.session.mixins.turns.estimate_tokens", lambda _text: 1)


def _reply(content: str = "done") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=5, processing_state="complete",
            timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=10,
    )


def _escalate_call(id_: str, scope: str = "workspace") -> tuple[str, str, str]:
    return id_, "EscalatePrivileges", json.dumps({"scope": scope})


def _tool_call_reply(calls: list[tuple[str, str, str]]) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="", role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=id_, name=name, arguments=args) for id_, name, args in calls]),
        prompt_tokens=10,
    )


def _session(mock_provider: MagicMock, *, process_config: ProcessConfig | None = None,
             workspace_path: Path | None = None) -> Session:
    pc = process_config if process_config is not None else ProcessConfig()
    config = SessionConfig(
        model="some/model",
        workspace=Workspace(path=workspace_path or Path("/tmp/fake-workspace")),
    )
    tool_registry = ToolRegistry.discover_tools(pc, config)
    return Session(config, provider=mock_provider, tool_registry=tool_registry, process_config=pc)


def _tool_response_envelope(session: Session) -> dict:
    tool_response = next(m for m in session.messages if m.role == "tool_response")
    assert isinstance(tool_response.content, str)
    envelope: dict = json.loads(tool_response.content)
    return envelope


def _tool_response_content(session: Session) -> str:
    """The failed-call's `error_message`, or the successful call's `response_body` rendered
    back to a string, for tests that only care about substring matches against either."""
    envelope = _tool_response_envelope(session)
    if envelope["is_error"]:
        return str(envelope["error_message"])
    return json.dumps(envelope["response_body"])


def test_no_callback_fails_closed() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1")]),
        _reply(),
    ]
    session = _session(mock_provider)

    response = session.send_turn("try it")

    assert response == "done"
    envelope = _tool_response_envelope(session)
    assert envelope["is_error"] is True
    assert envelope["error_category"] == "permission"
    assert "not approved" in _tool_response_content(session)


def test_invalid_scope_reports_error_without_invoking_callback() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1", scope="invalid_scope")]),
        _reply(),
    ]
    session = _session(mock_provider)
    on_escalate = MagicMock(return_value=EscalatePrivilegesDecision(approved=True))

    response = session.send_turn("try it", TurnEventHandlers(on_escalate_privileges=on_escalate))

    assert response == "done"
    envelope = _tool_response_envelope(session)
    assert envelope["is_error"] is True
    assert envelope["error_category"] == "validation"
    assert "Valid scopes" in _tool_response_content(session)
    on_escalate.assert_not_called()


def test_approved_records_scope_into_session_config() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1")]),
        _reply(),
    ]
    process_config = ProcessConfig()
    session = _session(mock_provider, process_config=process_config)
    assert "workspace" not in session.config.approved_scopes
    on_escalate = MagicMock(return_value=EscalatePrivilegesDecision(approved=True))

    response = session.send_turn("try it", TurnEventHandlers(on_escalate_privileges=on_escalate))

    assert response == "done"
    assert "workspace" in session.config.approved_scopes
    content = _tool_response_content(session)
    assert "approved" in content
    assert "workspace" in content
    on_escalate.assert_called_once()
    (ctx,), _ = on_escalate.call_args
    assert isinstance(ctx, EscalatePrivilegesContext)
    assert ctx.scope == "workspace"
    assert ".klorb" in ctx.description


def test_homedir_approved_records_scope_into_session_config() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1", scope="homedir")]),
        _reply(),
    ]
    process_config = ProcessConfig()
    session = _session(mock_provider, process_config=process_config)
    assert "homedir" not in session.config.approved_scopes
    on_escalate = MagicMock(return_value=EscalatePrivilegesDecision(approved=True))

    response = session.send_turn("try it", TurnEventHandlers(on_escalate_privileges=on_escalate))

    assert response == "done"
    assert "homedir" in session.config.approved_scopes
    content = _tool_response_content(session)
    assert "approved" in content
    assert "homedir" in content
    on_escalate.assert_called_once()
    (ctx,), _ = on_escalate.call_args
    assert isinstance(ctx, EscalatePrivilegesContext)
    assert ctx.scope == "homedir"
    assert str(KLORB_DATA_DIR) in ctx.description
    assert str(KLORB_STATE_DIR) in ctx.description


def test_denied_does_not_record_scope() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1")]),
        _reply(),
    ]
    process_config = ProcessConfig()
    session = _session(mock_provider, process_config=process_config)
    on_escalate = MagicMock(return_value=EscalatePrivilegesDecision(approved=False))

    response = session.send_turn("try it", TurnEventHandlers(on_escalate_privileges=on_escalate))

    assert response == "done"
    assert "workspace" not in session.config.approved_scopes
    envelope = _tool_response_envelope(session)
    assert envelope["is_error"] is True
    assert envelope["error_category"] == "permission"
    assert "denied" in _tool_response_content(session)


def test_escalation_succeeds_without_process_config(tmp_path: Path) -> None:
    """Escalation is session-scoped: `approved_scopes` lives on `SessionConfig`, so a
    `Session` constructed without a `ProcessConfig` can still record an approval into its
    own `config.approved_scopes` and report success back to the model."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_escalate_call("call_1")]),
        _reply(),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    tool_registry = ToolRegistry.discover_tools(ProcessConfig(), config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)
    on_escalate = MagicMock(return_value=EscalatePrivilegesDecision(approved=True))

    response = session.send_turn("try it", TurnEventHandlers(on_escalate_privileges=on_escalate))

    assert response == "done"
    assert "workspace" in session.config.approved_scopes
    content = _tool_response_content(session)
    assert "approved" in content
    on_escalate.assert_called_once()
