# © Copyright 2026 Aaron Kimball
"""Tests for the `AdditionalTools` interjection
(`Session._build_additional_tools_interjection`/`Session.send_turn`)."""
from collections.abc import Callable
from datetime import datetime
from unittest.mock import MagicMock

from klorb.api_provider import ProviderResponse
from klorb.message import Message
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.registry import ToolRegistry


def _build(
    make_session_config: Callable[..., SessionConfig], *,
    provider: MagicMock | None = None, with_tool_registry: bool = True,
) -> Session:
    config = make_session_config(model="some/model")
    process_config = ProcessConfig()
    tool_registry = ToolRegistry.discover_tools(process_config, config) if with_tool_registry else None
    return Session(
        config, provider=provider if provider is not None else MagicMock(),
        process_config=process_config, tool_registry=tool_registry)


def _reply(content: str = "ok") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=0, processing_state="complete",
            timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=0,
    )


def test_no_tool_registry_returns_none(make_session_config: Callable[..., SessionConfig]) -> None:
    session = _build(make_session_config, with_tool_registry=False)
    assert session._build_additional_tools_interjection() is None


def test_lists_replace_all_by_name_with_truncated_description(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _build(make_session_config)

    body = session._build_additional_tools_interjection()

    assert body is not None
    assert "SearchTools" in body
    assert '"name": "ReplaceAll"' in body


def test_send_turn_prepends_additional_tools_interjection_once(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = _build(make_session_config, provider=mock_provider)

    session.send_turn("first")
    session.send_turn("second")

    user_msgs = [m for m in session.messages if m.role == "user"]
    assert len(user_msgs) == 2
    assert '<SystemInterjection subject="AdditionalTools">' in user_msgs[0].content
    assert "ReplaceAll" in user_msgs[0].content
    assert '<SystemInterjection subject="AdditionalTools">' not in user_msgs[1].content
    assert session._additional_tools_seeded is True
