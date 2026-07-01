# © Copyright 2026 Aaron Kimball
"""Tests for klorb.openrouter."""

from unittest.mock import MagicMock

import pytest

from klorb import openrouter


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
        MagicMock(message=MagicMock(content="hello there")),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt("hi", model="some/model")

    assert result == "hello there"
    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "user", "content": "hi"}],
        extra_body=None,
    )


def test_send_prompt_uses_default_model_when_unspecified() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="hi there")),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt("hi")

    mock_client.chat.completions.create.assert_called_once_with(
        model=openrouter.DEFAULT_MODEL,
        messages=[{"role": "user", "content": "hi"}],
        extra_body=None,
    )


def test_send_prompt_includes_session_id_in_request_body() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content="hello there")),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt("hi", model="some/model", session_id="2026-06-30-10-00-happy-otter")

    assert result == "hello there"
    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "user", "content": "hi"}],
        extra_body={"session_id": "2026-06-30-10-00-happy-otter"},
    )
