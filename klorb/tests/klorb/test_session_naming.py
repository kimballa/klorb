# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session_naming: LLM-derived session titles. See
docs/specs/session-and-turns.md's "Session naming" section.
"""
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from unittest.mock import MagicMock

from fixtures.sample_models import sample_model_registry

from klorb.api_provider import ProviderResponse
from klorb.message import Message
from klorb.process_config import DEFAULT_SESSION_CLASSIFIER_MODEL
from klorb.session import Session, SessionConfig
from klorb.session_naming import (
    _response_format,
    default_naming_model,
    generate_session_name,
    thinking_effort_for,
)


def _reply(content: str) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=1, timestamp=datetime.now(),
            processing_state="complete"),
        prompt_tokens=1)


def _valid_name_json(title: str = "Fix auth bug") -> str:
    return json.dumps({"title": title})


# --- generate_session_name: success/failure/retry ---


def test_generate_session_name_returns_name_on_success() -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply(_valid_name_json())

    name = generate_session_name(
        "please fix the auth bug", api_provider=provider, model="some/model", timeout=5.0,
        e2e_timeout=10.0)

    assert name is not None
    assert name.title == "Fix auth bug"
    provider.send_prompt.assert_called_once()


def test_generate_session_name_passes_model_timeout_and_response_format() -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply(_valid_name_json())

    generate_session_name(
        "hello", api_provider=provider, model="openai/gpt-5-nano", timeout=5.0, e2e_timeout=10.0)

    _, kwargs = provider.send_prompt.call_args
    assert kwargs["model"] == "openai/gpt-5-nano"
    assert kwargs["timeout"] == 5.0
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["name"] == "SessionName"


def test_generate_session_name_forwards_reasoning_when_given() -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply(_valid_name_json())

    generate_session_name(
        "hello", api_provider=provider, model="some/model", timeout=5.0, e2e_timeout=10.0,
        reasoning={"effort": "low"})

    _, kwargs = provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"effort": "low"}


def test_generate_session_name_omits_reasoning_by_default() -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply(_valid_name_json())

    generate_session_name(
        "hello", api_provider=provider, model="some/model", timeout=5.0, e2e_timeout=10.0)

    _, kwargs = provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


def test_generate_session_name_retries_once_on_malformed_json_then_succeeds() -> None:
    provider = MagicMock()
    provider.send_prompt.side_effect = [_reply("not json at all"), _reply(_valid_name_json())]

    name = generate_session_name(
        "hello", api_provider=provider, model="some/model", timeout=5.0, e2e_timeout=10.0)

    assert name is not None
    assert provider.send_prompt.call_count == 2


def test_generate_session_name_gives_up_after_one_retry() -> None:
    provider = MagicMock()
    provider.send_prompt.side_effect = [_reply("not json"), _reply("still not json")]

    name = generate_session_name(
        "hello", api_provider=provider, model="some/model", timeout=5.0, e2e_timeout=10.0)

    assert name is None
    assert provider.send_prompt.call_count == 2


def test_generate_session_name_returns_none_immediately_on_request_error_without_retry() -> None:
    provider = MagicMock()
    provider.send_prompt.side_effect = RuntimeError("network error")

    name = generate_session_name(
        "hello", api_provider=provider, model="some/model", timeout=5.0, e2e_timeout=10.0)

    assert name is None
    assert provider.send_prompt.call_count == 1


def test_generate_session_name_cancels_a_stream_that_exceeds_the_e2e_deadline() -> None:
    provider = MagicMock()

    def _blocking_send_prompt(
        *_args: object, cancel_event: threading.Event, **_kwargs: object,
    ) -> ProviderResponse:
        if cancel_event.wait(timeout=5.0):
            raise RuntimeError("stream aborted by cancel_event")
        return _reply(_valid_name_json())

    provider.send_prompt.side_effect = _blocking_send_prompt

    started = time.perf_counter()
    name = generate_session_name(
        "hello", api_provider=provider, model="some/model", timeout=5.0, e2e_timeout=0.1)
    elapsed = time.perf_counter() - started

    assert name is None
    assert elapsed < 2.0, "should return at the e2e deadline, not after the full per-read timeout"


def test_generate_session_name_never_raises_on_a_completely_unmocked_provider() -> None:
    provider = MagicMock()

    name = generate_session_name(
        "hello", api_provider=provider, model="some/model", timeout=5.0, e2e_timeout=10.0)

    assert name is None


def test_response_format_sets_additional_properties_false() -> None:
    schema = _response_format()["json_schema"]["schema"]
    assert schema["additionalProperties"] is False


# --- default_naming_model ---


def test_default_naming_model_picks_the_capability_tagged_model(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock())

    assert default_naming_model(session) == "openai/gpt-5-nano"


def test_default_naming_model_falls_back_when_no_model_declares_the_capability(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock(), model_registry=sample_model_registry())

    assert default_naming_model(session) == DEFAULT_SESSION_CLASSIFIER_MODEL


# --- thinking_effort_for ---


def test_thinking_effort_for_returns_low_effort_for_a_thinking_capable_model(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock(), model_registry=sample_model_registry())

    assert thinking_effort_for(session, "beta") == {"effort": "low"}


def test_thinking_effort_for_returns_none_for_a_non_thinking_model(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock(), model_registry=sample_model_registry())

    assert thinking_effort_for(session, "alpha") is None


def test_thinking_effort_for_returns_none_for_an_unregistered_model_name(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock(), model_registry=sample_model_registry())

    assert thinking_effort_for(session, "not/a-registered-model") is None
