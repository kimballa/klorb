# © Copyright 2026 Aaron Kimball
"""Tests for klorb.openrouter."""

from datetime import datetime
from unittest.mock import MagicMock
from unittest.mock import patch

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
    tool_calls: list[MagicMock] | None = None,
) -> MagicMock:
    has_choice = content is not None or finish_reason is not None or reasoning is not None or tool_calls
    return MagicMock(
        choices=[
            MagicMock(
                delta=MagicMock(content=content, reasoning=reasoning, tool_calls=tool_calls),
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


def test_build_api_messages_excludes_thinking_role_messages() -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _reply_chunks("hi there")
    provider = openrouter.OpenRouterApiProvider(client=mock_client)
    thinking_message = Message(
        content="reasoning...", role="thinking", num_tokens=0, processing_state="complete",
        timestamp=datetime.now())

    provider.send_prompt([_user_message(), thinking_message], model="some/model")

    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


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
