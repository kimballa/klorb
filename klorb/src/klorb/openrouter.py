# © Copyright 2026 Aaron Kimball
"""Client for sending prompts to models hosted on OpenRouter via the OpenAI SDK."""

import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any
from typing import cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from klorb.api_provider import ApiProvider
from klorb.api_provider import ProviderResponse
from klorb.api_provider import ResponseAborted
from klorb.message import Message
from klorb.message import ToolCallRequest

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV_VAR = "OPENROUTER_API_KEY"
DEFAULT_MODEL = "openai/gpt-5-nano"

logger = logging.getLogger(__name__)


class OpenRouterApiProvider(ApiProvider):
    """Sends prompts to OpenRouter-hosted models using the OpenAI SDK."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = OPENROUTER_BASE_URL,
        client: OpenAI | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._client = client

    def get_api_key(self) -> str:
        """Read the OpenRouter API key, raising if it isn't configured."""
        api_key = self._api_key or os.environ.get(OPENROUTER_API_KEY_ENV_VAR)
        if not api_key:
            logger.error("%s is not set; cannot authenticate with OpenRouter.", OPENROUTER_API_KEY_ENV_VAR)
            raise RuntimeError(
                f"{OPENROUTER_API_KEY_ENV_VAR} is not set. Set it to your OpenRouter API key.")
        return api_key

    def build_client(self) -> OpenAI:
        """Construct (and cache) an OpenAI SDK client pointed at the OpenRouter API."""
        if self._client is None:
            logger.debug("Building OpenAI client for OpenRouter at %s", self._base_url)
            self._client = OpenAI(base_url=self._base_url, api_key=self.get_api_key())
        return self._client

    def send_prompt(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        model: str | None = None,
        session_id: str | None = None,
        reasoning: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProviderResponse:
        """Send the given conversation history to a model via OpenRouter, streaming the
        reply and invoking `on_chunk` per text delta and `on_thinking_chunk` per
        reasoning/thinking delta, and return the fully-assembled reply.

        If `tools` is given and non-empty, it's offered to the model as the OpenAI-style
        function-calling `tools` array; any tool calls the model requests are streamed in as
        `delta.tool_calls` fragments (per SDK index, an `id`/`function.name` sent once and
        `function.arguments` accumulated piecemeal), reassembled here, and reported as
        `ToolCallRequest`s on the returned reply's `Message.tool_calls`.

        If `cancel_event` is given and set while the stream is being consumed, the
        underlying HTTP stream is closed and `ResponseAborted` is raised.
        """
        resolved_model = model or self._model
        api_messages = self._build_api_messages(messages, system_prompt)
        logger.info("Sending %d-message turn to %s", len(api_messages), resolved_model)
        client = self.build_client()
        extra_body: dict[str, Any] | None = None
        if session_id is not None or reasoning is not None:
            extra_body = {}
            if session_id is not None:
                extra_body["session_id"] = session_id
            if reasoning is not None:
                extra_body["reasoning"] = reasoning
        create_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": api_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": extra_body,
        }
        if tools:
            create_kwargs["tools"] = tools
        stream = client.chat.completions.create(**create_kwargs)

        content_parts: list[str] = []
        finish_reason: str | None = None
        completion_tokens = 0
        prompt_tokens = 0
        tool_call_accumulators: dict[int, dict[str, str]] = {}
        for chunk in stream:
            if cancel_event is not None and cancel_event.is_set():
                stream.close()
                logger.info("Aborted streamed response from %s", resolved_model)
                raise ResponseAborted()
            if chunk.choices:
                choice = chunk.choices[0]
                delta_text = choice.delta.content or ""
                if delta_text:
                    content_parts.append(delta_text)
                    if on_chunk is not None:
                        on_chunk(delta_text)
                thinking_delta_text = getattr(choice.delta, "reasoning", None) or ""
                if thinking_delta_text and on_thinking_chunk is not None:
                    on_thinking_chunk(thinking_delta_text)
                for delta_call in choice.delta.tool_calls or []:
                    accumulator = tool_call_accumulators.setdefault(
                        delta_call.index, {"id": "", "name": "", "arguments": ""})
                    if delta_call.id:
                        accumulator["id"] = delta_call.id
                    if delta_call.function is not None:
                        if delta_call.function.name:
                            accumulator["name"] = delta_call.function.name
                        if delta_call.function.arguments:
                            accumulator["arguments"] += delta_call.function.arguments
                if choice.finish_reason is not None:
                    finish_reason = choice.finish_reason
            if chunk.usage is not None:
                completion_tokens = chunk.usage.completion_tokens
                prompt_tokens = chunk.usage.prompt_tokens

        content = "".join(content_parts)
        tool_calls = [
            ToolCallRequest(
                id=accumulator["id"], name=accumulator["name"], arguments=accumulator["arguments"])
            for _, accumulator in sorted(tool_call_accumulators.items())
        ] or None
        logger.debug(
            "Received %d-character response from %s (finish_reason=%s, completion_tokens=%d, "
            "prompt_tokens=%d, tool_calls=%s)",
            len(content), resolved_model, finish_reason, completion_tokens, prompt_tokens,
            [call.name for call in tool_calls] if tool_calls else None,
        )
        reply = Message(
            content=content,
            role="assistant",
            num_tokens=completion_tokens,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason=finish_reason,
            tool_calls=tool_calls,
        )
        return ProviderResponse(message=reply, prompt_tokens=prompt_tokens)

    @staticmethod
    def _build_api_messages(
        messages: list[Message], system_prompt: str | None
    ) -> list[ChatCompletionMessageParam]:
        """Convert session history (plus an optional system prompt) into OpenAI SDK message
        dicts. `"thinking"`-, `"tool_defs"`-, and `"system"`-role messages are omitted: none
        is replayed as-is here — reasoning content isn't meant to be replayed as conversation
        history, tool definitions are offered via the request's separate `tools` array (see
        `send_prompt`) rather than as a chat message, and a stored `"system"` message is only
        `klorb.session.Session`'s bookkeeping record of what was sent on the first turn (see
        `Session._ensure_system_message`) — the live system prompt is supplied fresh via the
        `system_prompt` argument below instead, so it reflects the current active model even
        if it's changed since that bookkeeping message was inserted. `"tool_use"` and
        `"tool_response"` are translated into the shapes the API expects for a tool-calling
        round trip: an `assistant` message carrying `tool_calls`, and a `tool` message
        carrying `tool_call_id`, respectively.
        """
        api_messages: list[ChatCompletionMessageParam] = []
        if system_prompt is not None:
            system_message = {"role": "system", "content": system_prompt}
            api_messages.append(cast(ChatCompletionMessageParam, system_message))
        for message in messages:
            if message.role in ("thinking", "tool_defs", "system"):
                continue
            if message.role == "tool_use":
                assistant_message: dict[str, Any] = {"role": "assistant", "content": message.content or None}
                if message.tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in message.tool_calls
                    ]
                api_messages.append(cast(ChatCompletionMessageParam, assistant_message))
            elif message.role == "tool_response":
                api_messages.append(cast(ChatCompletionMessageParam, {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }))
            else:
                api_messages.append(
                    cast(ChatCompletionMessageParam, {"role": message.role, "content": message.content}))
        return api_messages
