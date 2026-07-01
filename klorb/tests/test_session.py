# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session."""

import re
from datetime import datetime
from unittest import mock
from unittest.mock import MagicMock

import fixtures.sample_models as sample_models_package
import fixtures.sample_tools as sample_tools_package
import pytest

from klorb.api_provider import ProviderResponse
from klorb.message import Message
from klorb.message import ToolCallRequest
from klorb.models.registry import ModelRegistry
from klorb.process_config import ProcessConfig
from klorb.session import DEFAULT_MAX_TOOL_CALLS_PER_TURN
from klorb.session import THINKING_EFFORT_TOKEN_BUDGETS
from klorb.session import MAX_TOOL_CALL_ROUNDS
from klorb.session import Session
from klorb.session import SessionConfig
from klorb.session import ThinkingEffort
from klorb.session import ToolCallLimitExceeded
from klorb.session import generate_session_id
from klorb.tools.registry import ToolRegistry

SESSION_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-[a-z]+-[a-z]+$")


def _reply(content: str = "model reply", num_tokens: int = 5, prompt_tokens: int = 10) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content,
            role="assistant",
            num_tokens=num_tokens,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason="stop",
        ),
        prompt_tokens=prompt_tokens,
    )


def _tool_call_reply(
    calls: list[tuple[str, str, str]], num_tokens: int = 3, prompt_tokens: int = 10,
) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="",
            role="assistant",
            num_tokens=num_tokens,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=id_, name=name, arguments=args) for id_, name, args in calls],
        ),
        prompt_tokens=prompt_tokens,
    )


def _sample_tool_registry(config: SessionConfig) -> ToolRegistry:
    return ToolRegistry(ProcessConfig(), config, package=sample_tools_package)


def test_session_config_defaults() -> None:
    config = SessionConfig()

    assert config.interactive is True
    assert config.thinking_enabled is True
    assert config.thinking_effort == "high"


def test_session_saves_config() -> None:
    config = SessionConfig(model="some/model", interactive=False)
    session = Session(config, provider=MagicMock())

    assert session.config is config


def test_generate_session_id_matches_expected_format() -> None:
    assert SESSION_ID_RE.match(generate_session_id())


def test_generate_session_id_is_unique_across_calls() -> None:
    assert generate_session_id() != generate_session_id()


def test_session_generates_id_when_not_given_explicitly() -> None:
    config = SessionConfig()
    session = Session(config, provider=MagicMock())

    assert SESSION_ID_RE.match(session.id)


def test_session_uses_explicitly_given_id() -> None:
    config = SessionConfig()
    session = Session(config, provider=MagicMock(), session_id="my-custom-id")

    assert session.id == "my-custom-id"


def test_total_tokens_used_sums_recorded_message_tokens() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply(num_tokens=5, prompt_tokens=10)
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert session.total_tokens_used() == 10 + 5


def test_max_context_window_reads_registered_model_capabilities() -> None:
    config = SessionConfig(model="alpha")
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=MagicMock(), model_registry=registry)

    assert session.max_context_window() == 8_000


def test_max_context_window_none_when_model_unregistered() -> None:
    config = SessionConfig(model="some/unregistered-model")
    session = Session(config, provider=MagicMock())

    assert session.max_context_window() is None


def test_active_model_name_falls_back_to_config_model_when_unregistered() -> None:
    config = SessionConfig(model="some/unregistered-model")
    session = Session(config, provider=MagicMock())

    assert session.active_model_name() == "some/unregistered-model"


def test_active_model_name_invokes_registered_model_name() -> None:
    config = SessionConfig(model="alpha")
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=MagicMock(), model_registry=registry)

    assert session.active_model_name() == "alpha"


def test_send_turn_sends_prompt_to_active_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")

    response = session.send_turn("hi")

    assert response == "model reply"
    mock_provider.send_prompt.assert_called_once_with(
        session.messages[:-1], system_prompt=None, model="some/model", session_id="my-session-id",
        reasoning=None, tools=None, on_chunk=mock.ANY, on_thinking_chunk=mock.ANY, cancel_event=None)
    assert [m.content for m in session.messages] == ["hi", "model reply"]


def test_run_one_shot_delegates_to_send_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")

    response = session.run_one_shot("hi")

    assert response == "model reply"
    mock_provider.send_prompt.assert_called_once_with(
        session.messages[:-1], system_prompt=None, model="some/model", session_id="my-session-id",
        reasoning=None, tools=None, on_chunk=mock.ANY, on_thinking_chunk=mock.ANY, cancel_event=None)


def test_send_turn_passes_system_prompt_from_registered_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="alpha")
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["system_prompt"] == "You are Alpha."


def test_reasoning_defaults_to_high_effort_for_effort_style_thinking_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    session = Session(SessionConfig(model="beta"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"effort": "high"}


def test_reasoning_respects_configured_effort_level() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    config = SessionConfig(model="beta", thinking_effort="low")
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"effort": "low"}


def test_reasoning_uses_token_budget_for_tokens_style_thinking_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    session = Session(SessionConfig(model="gamma"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"max_tokens": THINKING_EFFORT_TOKEN_BUDGETS["high"]}


def test_reasoning_uses_custom_token_budgets_when_given() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    custom_budgets: dict[ThinkingEffort, int] = {"low": 1_000, "medium": 2_000, "high": 3_000}
    session = Session(
        SessionConfig(model="gamma"), provider=mock_provider, model_registry=registry,
        thinking_token_budgets=custom_budgets)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"max_tokens": 3_000}
    assert session.thinking_token_budgets == custom_budgets


def test_reasoning_none_when_thinking_disabled() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    config = SessionConfig(model="beta", thinking_enabled=False)
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


def test_reasoning_none_when_model_does_not_support_thinking() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    session = Session(SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


def test_reasoning_none_when_model_unregistered() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(SessionConfig(model="some/unregistered-model"), provider=mock_provider)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


def test_send_turn_sends_full_history_to_provider() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("r1", num_tokens=5, prompt_tokens=10), _reply("r2")]
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("first")
    session.send_turn("second")

    second_call_messages = mock_provider.send_prompt.call_args_list[1].args[0]
    assert [m.content for m in second_call_messages] == ["first", "r1", "second"]


def test_token_delta_accounted_across_two_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _reply("r1", num_tokens=5, prompt_tokens=10),
        _reply("r2", num_tokens=8, prompt_tokens=23),
    ]
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("first")
    session.send_turn("second")

    user1, assistant1, user2, assistant2 = session.messages
    assert user1.num_tokens == 10
    assert assistant1.num_tokens == 5
    assert user2.num_tokens == 23 - (10 + 5)
    assert assistant2.num_tokens == 8


def test_send_turn_marks_user_message_error_and_reraises_on_provider_failure() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = RuntimeError("boom")
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    failed_message = session.messages[-1]
    assert failed_message.processing_state == "error"
    assert failed_message.last_error == "boom"


def test_retry_last_turn_mutates_same_message_on_success() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [RuntimeError("boom"), _reply("recovered")]
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    response = session.retry_last_turn()

    assert response == "recovered"
    assert len(session.messages) == 2
    user_message = session.messages[0]
    assert user_message.content == "hi"
    assert user_message.processing_state == "complete"
    assert user_message.last_error is None


def test_retry_last_turn_raises_when_nothing_errored() -> None:
    session = Session(SessionConfig(model="some/model"), provider=MagicMock())

    with pytest.raises(ValueError, match="No errored turn to retry."):
        session.retry_last_turn()


def test_streaming_chunks_populate_and_finalize_placeholder_message() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_chunk("Hel")
        on_chunk("lo")
        return _reply("Hello", num_tokens=2, prompt_tokens=10)

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert len(session.messages) == 2
    assistant_message = session.messages[-1]
    assert assistant_message.content == "Hello"
    assert assistant_message.streaming_content is None
    assert assistant_message.processing_state == "complete"
    assert assistant_message.num_tokens == 2


def test_send_turn_forwards_chunks_to_caller_on_chunk() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_chunk("Hel")
        on_chunk("lo")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)
    spy = MagicMock()

    session.send_turn("hi", on_chunk=spy)

    assert [call.args[0] for call in spy.call_args_list] == ["Hel", "lo"]


def test_streaming_thinking_chunks_populate_and_finalize_a_separate_placeholder_message() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_thinking_chunk("Let ")
        on_thinking_chunk("me think.")
        on_chunk("Hello")
        return _reply("Hello", num_tokens=2, prompt_tokens=10)

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert len(session.messages) == 3
    user_message, thinking_message, assistant_message = session.messages
    assert user_message.role == "user"
    assert thinking_message.role == "thinking"
    assert thinking_message.content == "Let me think."
    assert thinking_message.streaming_content is None
    assert thinking_message.processing_state == "complete"
    assert thinking_message.num_tokens == 0
    assert assistant_message.role == "assistant"
    assert assistant_message.content == "Hello"


def test_send_turn_forwards_thinking_chunks_to_caller_on_thinking_chunk() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_thinking_chunk("Let ")
        on_thinking_chunk("me think.")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)
    spy = MagicMock()

    session.send_turn("hi", on_thinking_chunk=spy)

    assert [call.args[0] for call in spy.call_args_list] == ["Let ", "me think."]


def test_mid_stream_failure_marks_user_and_partial_assistant_message_error() -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_chunk("partial")
        raise RuntimeError("boom")

    mock_provider.send_prompt.side_effect = failing_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    user_message, assistant_message = session.messages
    assert user_message.processing_state == "error"
    assert user_message.last_error == "boom"
    assert assistant_message.processing_state == "error"
    assert assistant_message.last_error == "boom"
    assert assistant_message.streaming_content == ["partial"]


def test_mid_stream_failure_marks_thinking_placeholder_error_too() -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_thinking_chunk("partial thought")
        raise RuntimeError("boom")

    mock_provider.send_prompt.side_effect = failing_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    user_message, thinking_message = session.messages
    assert user_message.processing_state == "error"
    assert thinking_message.processing_state == "error"
    assert thinking_message.last_error == "boom"
    assert thinking_message.streaming_content == ["partial thought"]


def test_retry_last_turn_discards_partial_assistant_fragment_and_recovers() -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_chunk("partial")
        raise RuntimeError("boom")

    mock_provider.send_prompt.side_effect = failing_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    assert len(session.messages) == 2

    mock_provider.send_prompt.side_effect = None
    mock_provider.send_prompt.return_value = _reply("recovered")

    response = session.retry_last_turn()

    assert response == "recovered"
    assert len(session.messages) == 2
    assert session.messages[0].processing_state == "complete"
    assert session.messages[1].content == "recovered"


def test_no_tools_offered_when_tool_registry_unset() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["tools"] is None


def test_tool_definitions_offered_to_provider_when_tool_registry_set() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert {d["function"]["name"] for d in kwargs["tools"]} == {"echo", "add"}


def test_tool_defs_message_inserted_before_first_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("hi")

    assert session.messages[0].role == "tool_defs"
    assert [m.role for m in session.messages] == ["tool_defs", "user", "assistant"]


def test_tool_defs_message_not_duplicated_across_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("r1"), _reply("r2")]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("first")
    session.send_turn("second")

    assert sum(1 for m in session.messages if m.role == "tool_defs") == 1


def test_no_tool_defs_message_when_tool_registry_unset() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert all(m.role != "tool_defs" for m in session.messages)


def test_tool_call_round_trip_dispatches_tool_and_returns_final_reply() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi there"}')]),
        _reply("final answer", num_tokens=4, prompt_tokens=20),
    ]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response = session.send_turn("please echo")

    assert response == "final answer"
    roles = [m.role for m in session.messages]
    assert roles == ["tool_defs", "user", "tool_use", "tool_response", "assistant"]
    tool_use_message = session.messages[2]
    assert tool_use_message.tool_calls == [
        ToolCallRequest(id="call_1", name="echo", arguments='{"message": "hi there"}')]
    tool_response_message = session.messages[3]
    assert tool_response_message.tool_call_id == "call_1"
    assert tool_response_message.content == "hi there"
    user_message = session.messages[1]
    assert user_message.processing_state == "complete"
    assert user_message.num_tokens == 20
    assert mock_provider.send_prompt.call_count == 2


def test_tool_call_round_trip_forwards_tool_calls_to_second_request() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    second_call_messages = mock_provider.send_prompt.call_args_list[1].args[0]
    assert [m.role for m in second_call_messages] == ["tool_defs", "user", "tool_use", "tool_response"]


def test_unknown_tool_call_reports_error_to_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "NoSuchTool", "{}")]),
        _reply("recovered"),
    ]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response = session.send_turn("call a bogus tool")

    assert response == "recovered"
    tool_response_message = session.messages[3]
    assert tool_response_message.role == "tool_response"
    assert tool_response_message.content.startswith("Error:")


def test_round_limit_exceeded_raises_and_marks_user_message_error() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(
        config, provider=mock_provider, tool_registry=tool_registry,
        max_tool_calls_per_turn=1_000, max_tool_calls_per_session=1_000)

    with pytest.raises(ToolCallLimitExceeded):
        session.send_turn("loop forever")

    assert mock_provider.send_prompt.call_count == MAX_TOOL_CALL_ROUNDS + 1
    user_message = session.messages[1]
    assert user_message.processing_state == "error"
    assert "10" in (user_message.last_error or "")


def test_per_turn_tool_call_limit_defaults_to_five() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ToolCallLimitExceeded, match="5 tool call"):
        session.send_turn("loop forever")

    tool_response_messages = [m for m in session.messages if m.role == "tool_response"]
    assert len(tool_response_messages) == DEFAULT_MAX_TOOL_CALLS_PER_TURN
    user_message = session.messages[1]
    assert user_message.processing_state == "error"


def test_per_turn_tool_call_limit_is_configurable() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(
        config, provider=mock_provider, tool_registry=tool_registry, max_tool_calls_per_turn=2)

    with pytest.raises(ToolCallLimitExceeded, match="2 tool call"):
        session.send_turn("loop forever")

    tool_response_messages = [m for m in session.messages if m.role == "tool_response"]
    assert len(tool_response_messages) == 2


def test_per_turn_tool_call_limit_resets_between_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _reply("first done"),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
        _reply("second done"),
    ]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(
        config, provider=mock_provider, tool_registry=tool_registry, max_tool_calls_per_turn=1)

    response1 = session.send_turn("first")
    response2 = session.send_turn("second")

    assert response1 == "first done"
    assert response2 == "second done"


def test_per_session_tool_call_limit_accumulates_across_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _reply("first done"),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
    ]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(
        config, provider=mock_provider, tool_registry=tool_registry,
        max_tool_calls_per_turn=1_000, max_tool_calls_per_session=1)

    response1 = session.send_turn("first")
    assert response1 == "first done"

    with pytest.raises(ToolCallLimitExceeded, match="1 tool call"):
        session.send_turn("second")

    second_user_message = next(m for m in session.messages if m.role == "user" and m.content == "second")
    assert second_user_message.processing_state == "error"
