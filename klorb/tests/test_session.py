# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session."""

import re
from datetime import datetime
from unittest import mock
from unittest.mock import MagicMock

import fixtures.sample_models as sample_models_package
import pytest

from klorb.api_provider import ProviderResponse
from klorb.message import Message
from klorb.models.registry import ModelRegistry
from klorb.session import Session
from klorb.session import SessionConfig
from klorb.session import generate_session_id

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


def test_session_config_defaults() -> None:
    config = SessionConfig()

    assert config.interactive is True


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
        on_chunk=mock.ANY)
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
        on_chunk=mock.ANY)


def test_send_turn_passes_system_prompt_from_registered_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="alpha")
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["system_prompt"] == "You are Alpha."


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

    def fake_send_prompt(messages, system_prompt=None, model=None, session_id=None, on_chunk=None):
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

    def fake_send_prompt(messages, system_prompt=None, model=None, session_id=None, on_chunk=None):
        on_chunk("Hel")
        on_chunk("lo")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)
    spy = MagicMock()

    session.send_turn("hi", on_chunk=spy)

    assert [call.args[0] for call in spy.call_args_list] == ["Hel", "lo"]


def test_mid_stream_failure_marks_user_and_partial_assistant_message_error() -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(messages, system_prompt=None, model=None, session_id=None, on_chunk=None):
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


def test_retry_last_turn_discards_partial_assistant_fragment_and_recovers() -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(messages, system_prompt=None, model=None, session_id=None, on_chunk=None):
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
