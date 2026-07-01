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


def _chunk(
    content: str | None = None,
    finish_reason: str | None = None,
    usage: MagicMock | None = None,
) -> MagicMock:
    return MagicMock(
        choices=[MagicMock(delta=MagicMock(content=content), finish_reason=finish_reason)] if (
            content is not None or finish_reason is not None) else [],
        usage=usage,
    )


def _reply_chunks(content: str, completion_tokens: int = 1, prompt_tokens: int = 1) -> list[MagicMock]:
    usage = MagicMock(completion_tokens=completion_tokens, prompt_tokens=prompt_tokens)
    return [_chunk(content=content, finish_reason="stop", usage=usage)]


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
    mock_client.chat.completions.create.return_value = [
        _chunk(content="hello there"),
        _chunk(finish_reason="stop", usage=MagicMock(completion_tokens=7, prompt_tokens=3)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()], model="some/model")

    assert result.message.content == "hello there"
    assert result.message.num_tokens == 7
    assert result.message.finish_reason == "stop"
    assert result.prompt_tokens == 3
    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": True},
        extra_body=None,
    )


def test_send_prompt_uses_default_model_when_unspecified() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt([_user_message()])

    mock_client.chat.completions.create.assert_called_once_with(
        model=openrouter.DEFAULT_MODEL,
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": True},
        extra_body=None,
    )


def test_send_prompt_includes_session_id_in_request_body() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hello there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt(
        [_user_message()], model="some/model", session_id="2026-06-30-10-00-happy-otter")

    assert result.message.content == "hello there"
    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": True},
        extra_body={"session_id": "2026-06-30-10-00-happy-otter"},
    )


def test_send_prompt_prepends_system_prompt_when_given() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hello there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt([_user_message()], system_prompt="be nice", model="some/model")

    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "system", "content": "be nice"}, {"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": True},
        extra_body=None,
    )


def test_send_prompt_omits_system_message_when_not_given() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hello there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt([_user_message()], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_send_prompt_streams_content_across_multiple_chunks() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(content="Hel"),
        _chunk(content="lo"),
        _chunk(finish_reason="stop", usage=MagicMock(completion_tokens=2, prompt_tokens=5)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    on_chunk = MagicMock()

    result = provider.send_prompt([_user_message()], on_chunk=on_chunk)

    assert result.message.content == "Hello"
    assert [call.args[0] for call in on_chunk.call_args_list] == ["Hel", "lo"]


def test_send_prompt_reads_usage_from_choiceless_final_chunk() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(content="hi"),
        _chunk(usage=MagicMock(completion_tokens=4, prompt_tokens=9)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()])

    assert result.message.num_tokens == 4
    assert result.prompt_tokens == 9


def test_send_prompt_skips_on_chunk_for_empty_deltas() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(content=""),
        _chunk(content="hi", finish_reason="stop", usage=MagicMock(completion_tokens=1, prompt_tokens=1)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    on_chunk = MagicMock()

    provider.send_prompt([_user_message()], on_chunk=on_chunk)

    on_chunk.assert_called_once_with("hi")


def test_send_prompt_works_without_on_chunk() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(content="hi", finish_reason="stop", usage=MagicMock(completion_tokens=1, prompt_tokens=1)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()])

    assert result.message.content == "hi"
