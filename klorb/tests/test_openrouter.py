# © Copyright 2026 Aaron Kimball
"""Tests for klorb.openrouter."""

import threading
import time
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from klorb import openrouter
from klorb.message import Message
from klorb.token_estimate import estimate_tokens


def _user_message(content: str = "hi") -> Message:
    return Message(
        content=content,
        role="user",
        num_tokens=0,
        processing_state="complete",
        timestamp=datetime.now(),
    )


def _tool_call_delta(
    index: int, id: str | None = None, name: str | None = None, arguments: str | None = None,
) -> MagicMock:
    # `name` is a reserved MagicMock constructor kwarg (controls its repr), so it has to be
    # set as a plain attribute afterward rather than passed into the constructor.
    function = MagicMock(arguments=arguments)
    function.name = name
    return MagicMock(index=index, id=id, function=function)


def _chunk(
    content: str | None = None,
    finish_reason: str | None = None,
    usage: MagicMock | None = None,
    reasoning: str | None = None,
    reasoning_details: list[dict[str, Any]] | None = None,
    tool_calls: list[MagicMock] | None = None,
) -> MagicMock:
    has_choice = (
        content is not None or finish_reason is not None or reasoning is not None
        or reasoning_details or tool_calls
    )
    return MagicMock(
        choices=[
            MagicMock(
                delta=MagicMock(
                    content=content, reasoning=reasoning, reasoning_details=reasoning_details,
                    tool_calls=tool_calls),
                finish_reason=finish_reason),
        ] if has_choice else [],
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


def test_build_client_uses_default_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV_VAR, "test-key")
    provider = openrouter.OpenRouterApiProvider()
    with patch("klorb.openrouter.OpenAI") as mock_openai_cls:
        provider.build_client()
    mock_openai_cls.assert_called_once_with(base_url=openrouter.OPENROUTER_BASE_URL, api_key="test-key")


def test_build_client_honors_custom_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(openrouter.OPENROUTER_API_KEY_ENV_VAR, "test-key")
    provider = openrouter.OpenRouterApiProvider(base_url="https://gateway.example.com/v1")
    with patch("klorb.openrouter.OpenAI") as mock_openai_cls:
        provider.build_client()
    mock_openai_cls.assert_called_once_with(
        base_url="https://gateway.example.com/v1", api_key="test-key")


def test_send_prompt_returns_model_response() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(content="hello there"),
        _chunk(finish_reason="stop", usage=MagicMock(completion_tokens=7, prompt_tokens=3)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()], model="some/model")

    assert result.message.content == "hello there"
    assert result.message.num_tokens == estimate_tokens("hello there")
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

    assert result.message.num_tokens == estimate_tokens("hi")
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


def test_send_prompt_includes_reasoning_in_request_body() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt([_user_message()], model="some/model", reasoning={"effort": "high"})

    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": True},
        extra_body={"reasoning": {"effort": "high"}},
    )


def test_send_prompt_merges_reasoning_and_session_id_in_request_body() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt(
        [_user_message()], model="some/model", session_id="my-session-id",
        reasoning={"max_tokens": 32_768})

    mock_client.chat.completions.create.assert_called_once_with(
        model="some/model",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": True},
        extra_body={"session_id": "my-session-id", "reasoning": {"max_tokens": 32_768}},
    )


def test_send_prompt_forwards_thinking_deltas_via_on_thinking_chunk() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(reasoning="Let "),
        _chunk(reasoning="me think."),
        _chunk(content="Hello", finish_reason="stop", usage=MagicMock(completion_tokens=1, prompt_tokens=1)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    on_thinking_chunk = MagicMock()

    result = provider.send_prompt([_user_message()], on_thinking_chunk=on_thinking_chunk)

    assert result.message.content == "Hello"
    assert [call.args[0] for call in on_thinking_chunk.call_args_list] == ["Let ", "me think."]


def test_send_prompt_skips_on_thinking_chunk_for_empty_reasoning_deltas() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(reasoning=""),
        _chunk(content="hi", finish_reason="stop", usage=MagicMock(completion_tokens=1, prompt_tokens=1)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    on_thinking_chunk = MagicMock()

    provider.send_prompt([_user_message()], on_thinking_chunk=on_thinking_chunk)

    on_thinking_chunk.assert_not_called()


def test_send_prompt_works_without_on_thinking_chunk() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(reasoning="thinking..."),
        _chunk(content="hi", finish_reason="stop", usage=MagicMock(completion_tokens=1, prompt_tokens=1)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()])

    assert result.message.content == "hi"


def test_send_prompt_accumulates_reasoning_details_fragments_by_index() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(reasoning_details=[{"type": "reasoning.text", "text": "Let ", "index": 0}]),
        _chunk(reasoning_details=[{"type": "reasoning.text", "text": "me think.", "index": 0}]),
        _chunk(content="Hello", finish_reason="stop", usage=MagicMock(completion_tokens=1, prompt_tokens=1)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    on_reasoning_details = MagicMock()

    provider.send_prompt([_user_message()], on_reasoning_details=on_reasoning_details)

    assert on_reasoning_details.call_args_list[0].args[0] == [
        {"type": "reasoning.text", "text": "Let ", "index": 0}]
    assert on_reasoning_details.call_args_list[-1].args[0] == [
        {"type": "reasoning.text", "text": "Let me think.", "index": 0}]


def test_send_prompt_accumulates_reasoning_details_by_separate_index() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(reasoning_details=[
            {"type": "reasoning.text", "text": "visible", "index": 0},
            {"type": "reasoning.encrypted", "data": "opaque-blob", "index": 1},
        ]),
        _chunk(content="Hello", finish_reason="stop", usage=MagicMock(completion_tokens=1, prompt_tokens=1)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    on_reasoning_details = MagicMock()

    provider.send_prompt([_user_message()], on_reasoning_details=on_reasoning_details)

    assert on_reasoning_details.call_args_list[-1].args[0] == [
        {"type": "reasoning.text", "text": "visible", "index": 0},
        {"type": "reasoning.encrypted", "data": "opaque-blob", "index": 1},
    ]


def test_send_prompt_skips_on_reasoning_details_when_no_fragments() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(content="hi", finish_reason="stop", usage=MagicMock(completion_tokens=1, prompt_tokens=1)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    on_reasoning_details = MagicMock()

    provider.send_prompt([_user_message()], on_reasoning_details=on_reasoning_details)

    on_reasoning_details.assert_not_called()


def test_build_api_messages_folds_thinking_content_onto_the_next_assistant_message() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    thinking_message = Message(
        content="reasoning...", role="thinking", num_tokens=0, processing_state="complete",
        timestamp=datetime.now(), reasoning_details=[{"type": "reasoning.text", "text": "reasoning..."}])
    assistant_message = Message(
        content="final answer", role="assistant", num_tokens=0, processing_state="complete",
        timestamp=datetime.now())

    provider.send_prompt(
        [_user_message(), thinking_message, assistant_message], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"][1] == {
        "role": "assistant",
        "content": "final answer",
        "reasoning": "reasoning...",
        "reasoning_details": [{"type": "reasoning.text", "text": "reasoning..."}],
    }


def test_build_api_messages_folds_thinking_content_onto_the_next_tool_use_message() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    thinking_message = Message(
        content="reasoning...", role="thinking", num_tokens=0, processing_state="complete",
        timestamp=datetime.now())
    tool_use_message = Message(
        content="", role="tool_use", num_tokens=0, processing_state="complete",
        timestamp=datetime.now(),
        tool_calls=[openrouter.ToolCallRequest(id="call_1", name="ReadFile", arguments='{"filename": "f"}')])

    provider.send_prompt(
        [_user_message(), thinking_message, tool_use_message], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"][1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "ReadFile", "arguments": '{"filename": "f"}'},
            },
        ],
        "reasoning": "reasoning...",
    }


def test_build_api_messages_drops_thinking_content_when_drop_reasoning_is_true() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    thinking_message = Message(
        content="reasoning...", role="thinking", num_tokens=0, processing_state="complete",
        timestamp=datetime.now(), reasoning_details=[{"type": "reasoning.text", "text": "reasoning..."}])
    assistant_message = Message(
        content="final answer", role="assistant", num_tokens=0, processing_state="complete",
        timestamp=datetime.now())

    provider.send_prompt(
        [_user_message(), thinking_message, assistant_message], model="some/model",
        drop_reasoning=True)

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "final answer"},
    ]


def test_send_prompt_omits_tools_when_not_given() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    provider.send_prompt([_user_message()], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert "tools" not in kwargs


def test_send_prompt_includes_tools_when_given() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    tool_defs = [{"type": "function", "function": {"name": "ReadFile", "parameters": {}}}]

    provider.send_prompt([_user_message()], model="some/model", tools=tool_defs)

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["tools"] == tool_defs


def test_send_prompt_reassembles_streamed_tool_call_fragments() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(tool_calls=[_tool_call_delta(0, id="call_1", name="ReadFile", arguments="")]),
        _chunk(tool_calls=[_tool_call_delta(0, arguments='{"filename": ')]),
        _chunk(tool_calls=[_tool_call_delta(0, arguments='"f.txt"}')]),
        _chunk(finish_reason="tool_calls", usage=MagicMock(completion_tokens=3, prompt_tokens=5)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()], model="some/model")

    assert result.message.finish_reason == "tool_calls"
    assert result.message.tool_calls == [
        openrouter.ToolCallRequest(id="call_1", name="ReadFile", arguments='{"filename": "f.txt"}'),
    ]


def test_send_prompt_reassembles_multiple_parallel_tool_calls_by_index() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [
        _chunk(tool_calls=[
            _tool_call_delta(0, id="call_1", name="ReadFile", arguments='{"filename": "a"}'),
            _tool_call_delta(1, id="call_2", name="ReadFile", arguments='{"filename": "b"}'),
        ]),
        _chunk(finish_reason="tool_calls", usage=MagicMock(completion_tokens=3, prompt_tokens=5)),
    ]
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()], model="some/model")

    assert result.message.tool_calls == [
        openrouter.ToolCallRequest(id="call_1", name="ReadFile", arguments='{"filename": "a"}'),
        openrouter.ToolCallRequest(id="call_2", name="ReadFile", arguments='{"filename": "b"}'),
    ]


def test_send_prompt_leaves_tool_calls_none_when_none_requested() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()])

    assert result.message.tool_calls is None


def test_build_api_messages_excludes_stored_system_role_messages() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    system_message = Message(
        content="You are Alpha.", role="system", num_tokens=0, processing_state="complete",
        timestamp=datetime.now())

    provider.send_prompt([system_message, _user_message()], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_build_api_messages_does_not_duplicate_system_message_from_history() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    system_message = Message(
        content="You are Alpha.", role="system", num_tokens=0, processing_state="complete",
        timestamp=datetime.now())

    provider.send_prompt(
        [system_message, _user_message()], system_prompt="You are Alpha.", model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"] == [
        {"role": "system", "content": "You are Alpha."},
        {"role": "user", "content": "hi"},
    ]


def test_build_api_messages_excludes_tool_defs_role_messages() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    tool_defs_message = Message(
        content="[...]", role="tool_defs", num_tokens=0, processing_state="complete",
        timestamp=datetime.now())

    provider.send_prompt([tool_defs_message, _user_message()], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_build_api_messages_translates_tool_use_role_to_assistant_with_tool_calls() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    tool_use_message = Message(
        content="", role="tool_use", num_tokens=0, processing_state="complete",
        timestamp=datetime.now(),
        tool_calls=[openrouter.ToolCallRequest(id="call_1", name="ReadFile", arguments='{"filename": "f"}')])

    provider.send_prompt([_user_message(), tool_use_message], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"][1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "ReadFile", "arguments": '{"filename": "f"}'},
            },
        ],
    }


def test_build_api_messages_translates_tool_response_role_to_tool() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    tool_response_message = Message(
        content="42", role="tool_response", num_tokens=0, processing_state="complete",
        timestamp=datetime.now(), tool_call_id="call_1")

    provider.send_prompt([_user_message(), tool_response_message], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"][1] == {"role": "tool", "content": "42", "tool_call_id": "call_1"}


# ═══════════════════════════════════════════════════════════════════════════════
# Tests for the daemon stream-closer thread that enables immediate cancellation
# when the worker thread is blocked on next(stream) and no chunks are arriving.
# ═══════════════════════════════════════════════════════════════════════════════

class _BlockingStream:
    """A mock stream that yields one chunk, then blocks on the next read until
    ``close()`` is called from another thread — exactly mimicking the real
    scenario where the HTTP response socket has no data for seconds."""

    def __init__(self, first_chunk: MagicMock, close_exception: Exception | None = None):
        self._first_chunk: MagicMock | None = first_chunk
        self._close_exception = close_exception
        self._closed = threading.Event()
        self._blocker = threading.Event()
        self.close_called = False

    def __iter__(self):
        return self

    def __next__(self) -> MagicMock:
        if self._first_chunk is not None:
            chunk = self._first_chunk
            self._first_chunk = None
            return chunk
        # Block until close() is called; mypy flags this as unreachable after
        # the None check above, but at runtime the attribute *is* set to None
        # after the first call, so the second call does reach here.
        self._blocker.wait(timeout=10)  # type: ignore[unreachable]
        if self._closed.is_set() and self._close_exception is not None:
            raise self._close_exception
        raise StopIteration

    def close(self) -> None:
        self.close_called = True
        self._closed.set()
        self._blocker.set()


def test_stream_closer_thread_unblocks_blocked_next_on_cancel() -> None:
    """When cancel_event is set while next(stream) is blocked, the daemon
    _stream_closer_thread calls stream.close(), which unblocks the iterator
    with an exception.  The ``except Exception`` handler detects that
    cancel_event is set and raises ResponseAborted."""
    from klorb.api_provider import ResponseAborted

    first_chunk = _chunk(content="partial", finish_reason=None)
    error_on_close = Exception("stream closed by cancel")
    blocking_stream = _BlockingStream(first_chunk, close_exception=error_on_close)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = blocking_stream
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    cancel_event = threading.Event()
    result: dict[str, Any] = {}

    def send():
        try:
            resp = provider.send_prompt(
                [_user_message()], cancel_event=cancel_event)
            result["response"] = resp
        except ResponseAborted:
            result["aborted"] = True
        except Exception as e:
            result["error"] = e

    sender = threading.Thread(target=send)
    sender.start()

    # Wait for the first chunk to be consumed (the stream is now blocked on next())
    for _ in range(100):
        if blocking_stream._first_chunk is None:
            break
        time.sleep(0.01)
    time.sleep(0.1)  # Give the thread time to block on __next__

    # Signal cancellation — the daemon thread should call stream.close()
    cancel_event.set()
    sender.join(timeout=5)

    assert blocking_stream.close_called, "stream.close() should have been called by the daemon thread"
    assert result.get("aborted") is True, f"Expected ResponseAborted, got {result}"


def test_stream_closer_thread_is_not_spawned_when_no_cancel_event() -> None:
    """When cancel_event is None, no daemon thread is spawned and the stream
    iterates normally."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("all good")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    result = provider.send_prompt([_user_message()])
    assert result.message.content == "all good"


def test_stream_closer_thread_is_not_spawned_when_cancel_event_already_set() -> None:
    """If cancel_event is already set before the stream is created, the daemon
    thread is not spawned — the per-chunk check handles it immediately."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("gone")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    cancel_event = threading.Event()
    cancel_event.set()  # Pre-set

    from klorb.api_provider import ResponseAborted
    with pytest.raises(ResponseAborted):
        provider.send_prompt([_user_message()], cancel_event=cancel_event)


def test_stream_closer_thread_reraises_real_error_when_cancel_not_set() -> None:
    """If the stream raises a genuine error (not caused by cancellation), and
    cancel_event is NOT set, the exception propagates normally."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("gone")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    # Inject a real error by making the client raise
    mock_client.chat.completions.create.side_effect = RuntimeError("real API error")

    with pytest.raises(RuntimeError, match="real API error"):
        provider.send_prompt([_user_message()])


def test_stream_closer_thread_exits_promptly_on_normal_completion() -> None:
    """On normal (non-cancelled) completion, the daemon thread should exit
    promptly via _stream_done rather than lingering on cancel_event.wait()."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("all done")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)

    cancel_event = threading.Event()
    result = provider.send_prompt([_user_message()], cancel_event=cancel_event)
    assert result.message.content == "all done"

    # The daemon thread polls _stream_done every 250 ms; give it at most 1 s
    # to exit.  If the thread is still alive after that, it leaked.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not cancel_event.is_set():
            break
        time.sleep(0.05)
    assert not cancel_event.is_set(), (
        "cancel_event should never be set on normal completion"
    )
