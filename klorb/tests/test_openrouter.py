# © Copyright 2026 Aaron Kimball
"""Tests for klorb.openrouter."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from klorb import openrouter
from klorb.message import Message


def _user_message(content: str = "hi") -> Message:
    return Message(
        content=content,
        role="user",
        num_tokens=0,
        processing_state="complete",
        timestamp=datetime.now(),
    )


def test_get_api_key_returns_explicit_value() -> None:
    provider = openrouter.OpenRouterApiProvider(api_key="explicit-key")
    assert provider.get_api_key() == "explicit-key"


def test_get_api_key_returns_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV_VAR, "test-key")
    provider = openrouter.OpenRouterApiProvider()
    assert provider.get_api_key() == "test-key"


def test_get_api_key_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(openrouter.OPENROUTER_API_KEY_ENV_VAR, raising=False)
    provider = openrouter.OpenRouterApiProvider()
    with pytest.raises(RuntimeError):
        provider.get_api_key()


def test_send_prompt_returns_model_response() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="hello there"), finish_reason="stop"),
    ]
    mock_client.chat.completions.create.return_value.usage = MagicMock(completion_tokens=7, prompt_tokens=3)
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()], model="some/model")

    assert result.message.content == "hello there"
    assert result.message.num_tokens == 7
    assert result.message.finish_reason == "stop"
    assert result.prompt_tokens == 3
    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "user", "content": "hi"}],
        extra_body=None,
    )


def test_send_prompt_uses_default_model_when_unspecified() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="hi there"), finish_reason="stop"),
    ]
    mock_client.chat.completions.create.return_value.usage = MagicMock(completion_tokens=1, prompt_tokens=1)
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt([_user_message()])

    mock_client.chat.completions.create.assert_called_once_with(
        model=openrouter.DEFAULT_MODEL,
        messages=[{"role": "user", "content": "hi"}],
        extra_body=None,
    )


def test_send_prompt_includes_session_id_in_request_body() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="hello there"), finish_reason="stop"),
    ]
    mock_client.chat.completions.create.return_value.usage = MagicMock(completion_tokens=1, prompt_tokens=1)
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt(
        [_user_message()], model="some/model", session_id="2026-06-30-10-00-happy-otter")

    assert result.message.content == "hello there"
    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "user", "content": "hi"}],
        extra_body={"session_id": "2026-06-30-10-00-happy-otter"},
    )


def test_send_prompt_prepends_system_prompt_when_given() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="hello there"), finish_reason="stop"),
    ]
    mock_client.chat.completions.create.return_value.usage = MagicMock(completion_tokens=1, prompt_tokens=1)
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt([_user_message()], system_prompt="be nice", model="some/model")

    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "system", "content": "be nice"}, {"role": "user", "content": "hi"}],
        extra_body=None,
    )


def test_send_prompt_omits_system_message_when_not_given() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="hello there"), finish_reason="stop"),
    ]
    mock_client.chat.completions.create.return_value.usage = MagicMock(completion_tokens=1, prompt_tokens=1)
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt([_user_message()], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
