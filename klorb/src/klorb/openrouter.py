# © Copyright 2026 Aaron Kimball
"""Client for sending prompts to models hosted on OpenRouter via the OpenAI SDK."""

import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from klorb.api_provider import ApiProvider, ProviderResponse, ResponseAborted
from klorb.message import Message, ToolCallRequest
from klorb.token_estimate import estimate_tokens

# Set true if you want *extremely* verbose debug logs.
LOG_CONVERSATION_EVERY_TURN = False

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV_VAR = "OPENROUTER_API_KEY"
DEFAULT_MODEL = "xiaomi/mimo-v2.5"

_REASONING_DETAIL_INCREMENTAL_TEXT_FIELDS = ("text", "summary", "data")
"""`reasoning_details` entry fields that stream incrementally, fragment by fragment, and so
are concatenated across fragments sharing the same `index` rather than overwritten -- unlike
`"type"`/`"id"`/`"format"`/`"index"` themselves, which a provider repeats identically (or
sends only once) on every fragment for a given entry."""

logger = logging.getLogger(__name__)



def _log_api_error_context(
    exc: Exception,
    *,
    resolved_model: str,
    message_count: int,
    total_content_chars: int,
    has_tools: bool,
    has_reasoning: bool,
) -> None:
    """Log detailed context about an API error to help diagnose request issues.

    Extracts HTTP request/response bodies from OpenAI SDK exceptions when available,
    and logs a summary of what was being sent (model, message count, content size,
    etc.) alongside the full error details.  For400-level errors where the response
    body contains a JSON parse error message, also logs a truncated snapshot of the
    request body that was sent.
    """
    # --- basic context always logged ---
    logger.error(
        "API error for model=%s messages=%d content_chars=%d tools=%s reasoning=%s: %s",
        resolved_model, message_count, total_content_chars,
        has_tools, has_reasoning, exc,
        exc_info=True,
    )

    # --- extract HTTP request/response from SDK exceptions when available ---
    request_obj = getattr(exc, "request", None)
    response_obj = getattr(exc, "response", None)
    body = getattr(exc, "body", None)
    status_code = getattr(exc, "status_code", None)

    if response_obj is not None:
        try:
            resp_text = response_obj.text
            if len(resp_text) > 4096:
                resp_out = resp_text[:4096] + f"...(truncated, len={len(resp_text)})"
            else:
                resp_out = resp_text

            logger.error(
                "API HTTP response (status=%s, length=%d):\n%s",
                status_code or response_obj.status_code,
                len(resp_text),
                resp_out
            )
        except Exception:
            logger.warning("Could not read API HTTP response body", exc_info=True)

    if body is not None and body != getattr(response_obj, "text", None):
        logger.error("API error body: %r", body)

    # --- for 400 errors, try to dump the request body we actually sent ---
    if status_code == 400 or getattr(exc, "type", None) == "BadRequestError":
        if request_obj is not None:
            try:
                req_body_bytes = request_obj.content
                req_text = req_body_bytes.decode("utf-8", errors="replace")
                if len(req_text) > 8192:
                    req_out = req_text[:8192] + f"...(truncated, len={len(req_text)})"
                else:
                    req_out = req_text

                logger.error(
                    "Request body that triggered the 400 (%d chars):\n%s",
                    len(req_text),
                    req_out
                )
            except Exception:
                logger.warning("Could not read API request body", exc_info=True)

            # Log request metadata
            try:
                logger.error(
                    "Request metadata: url=%s method=%s content_type=%s content_length=%s",
                    request_obj.url,
                    request_obj.method,
                    request_obj.headers.get("content-type", "?"),
                    request_obj.headers.get("content-length", "?"),
                )
            except Exception:
                logger.warning("Could not read API request metadata", exc_info=True)


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
        session_id: str = "",
        reasoning: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
        timeout: float | None = None,
        drop_reasoning: bool = False,
        cache_mgmt_style: str = "AUTOMATIC",
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
        on_reasoning_details: Callable[[list[dict[str, Any]]], None] | None = None,
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

        `response_format`, if given, is folded into `extra_body` alongside `session_id`/
        `reasoning` (the same additive-request-body pattern all three already use), asking the
        underlying model for a structured-output reply rather than free-form text.

        `timeout`, if given, is passed straight through to the OpenAI SDK's own per-request
        `timeout` kwarg, bounding this one call's wall-clock time independent of any client-wide
        default.

        `drop_reasoning` controls whether prior turns' thinking content is included in
        `api_messages` at all -- see `_build_api_messages`.

        If `on_reasoning_details` is given, it's invoked with the current, fully-merged
        `reasoning_details` array (OpenRouter's structured reasoning payload -- see
        `Message.reasoning_details`) each time a chunk carries a new fragment of it, mirroring
        `delta.tool_calls`: fragments arrive keyed by `index` and are merged here
        (`_REASONING_DETAIL_INCREMENTAL_TEXT_FIELDS` concatenated, every other field
        overwritten) rather than left for the caller to reassemble. Each call gets a fresh
        snapshot (a new list of shallow-copied entry dicts) rather than a reference to the
        internal accumulator, so a caller that keeps an earlier call's payload around isn't
        surprised by it changing underfoot once a later fragment arrives.

        If `cancel_event` is given and set while the stream is being consumed, the
        underlying HTTP stream is closed and `ResponseAborted` is raised.
        """
        resolved_model = model or self._model
        api_messages = self._build_api_messages(messages, system_prompt, drop_reasoning)

        # Anthropic explicit caching: add cache_control to each content block
        if cache_mgmt_style == "ANTHROPIC_EXPLICIT":
            api_messages = self._apply_explicit_cache_control(api_messages)
        total_content_chars = sum(
            len(str(m.get("content") or "")) for m in api_messages
        )
        has_reasoning = any(m.get("reasoning") or m.get("reasoning_details") for m in api_messages)
        logger.info(
            "Sending %d-message turn to %s (content_chars=%d, tools=%s, reasoning=%s)",
            len(api_messages), resolved_model, total_content_chars,
            bool(tools), has_reasoning,
        )
        if LOG_CONVERSATION_EVERY_TURN and logger.isEnabledFor(logging.DEBUG):
            # Dump a compact snapshot of every message for diagnostics.  Each
            # message is truncated to 200 chars so a long system prompt doesn't
            # flood the log, but the first and last 100 chars are preserved so
            # we can see the start/end of the content.
            for idx, m in enumerate(api_messages):
                raw_content = m.get("content")
                content_str = str(raw_content or "")
                if content_str:
                    preview = content_str[:100]
                    if len(content_str) > 200:
                        preview += "..." + content_str[-100:]
                else:
                    preview = "(empty)"
                logger.debug(
                    "  api_messages[%d] role=%s"
                    " content_len=%d%s preview=%r",
                    idx, m.get("role", "?"),
                    len(content_str),
                    " has_reasoning"
                    if m.get("reasoning")
                    or m.get("reasoning_details")
                    else "",
                    preview,
                )
        client = self.build_client()
        extra_body: dict[str, Any] = {"session_id": session_id}
        if reasoning is not None:
            extra_body["reasoning"] = reasoning
        if response_format is not None:
            extra_body["response_format"] = response_format
        # Anthropic automatic caching: add cache_control once at the top-level request
        if cache_mgmt_style == "ANTHROPIC_AUTOMATIC":
            extra_body["cache_control"] = {"type": "ephemeral"}
        create_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": api_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": extra_body,
        }
        if tools:
            create_kwargs["tools"] = tools
        if timeout is not None:
            create_kwargs["timeout"] = timeout
        try:
            stream = client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            _log_api_error_context(
                exc,
                resolved_model=resolved_model,
                message_count=len(api_messages),
                total_content_chars=total_content_chars,
                has_tools=bool(tools),
                has_reasoning=has_reasoning,
            )
            raise

        # Spawn a daemon thread that watches cancel_event and closes the stream
        # when it fires.  This unblocks the blocked next() call inside
        # ``for chunk in stream:`` immediately — without this, the per-chunk
        # ``cancel_event.is_set()`` check only runs after the *next* chunk
        # arrives from the HTTP response, which can take seconds if the model
        # is still thinking/processing.
        _stream_done = threading.Event()
        _stream_closer_thread: threading.Thread | None = None
        if cancel_event is not None and not cancel_event.is_set():
            def _cancel_stream_when_signaled() -> None:
                # Poll until either cancel fires or the stream finishes
                # normally.  Waking every 250 ms is cheap and keeps the thread
                # from lingering after a successful response.
                while not _stream_done.wait(timeout=0.25):
                    if cancel_event.is_set():
                        try:
                            stream.close()
                        except Exception:
                            pass  # Best-effort; errors are harmless here.
                        return
            _stream_closer_thread = threading.Thread(
                target=_cancel_stream_when_signaled, daemon=True)
            _stream_closer_thread.start()

        content_parts: list[str] = []
        finish_reason: str | None = None
        completion_tokens = 0
        prompt_tokens = 0
        cached_tokens = 0
        total_cost = 0.0
        tool_call_accumulators: dict[int, dict[str, str]] = {}
        reasoning_details_accumulators: dict[int, dict[str, Any]] = {}
        try:
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
                    reasoning_details_fragments = getattr(choice.delta, "reasoning_details", None) or []
                    if reasoning_details_fragments:
                        for fragment in reasoning_details_fragments:
                            accumulator = reasoning_details_accumulators.setdefault(
                                fragment.get("index", 0), {})
                            for key, value in fragment.items():
                                existing = accumulator.get(key)
                                if (
                                    key in _REASONING_DETAIL_INCREMENTAL_TEXT_FIELDS
                                    and isinstance(value, str) and isinstance(existing, str)
                                ):
                                    accumulator[key] = existing + value
                                else:
                                    accumulator[key] = value
                        if on_reasoning_details is not None:
                            on_reasoning_details([
                                dict(accumulator) for _, accumulator
                                in sorted(reasoning_details_accumulators.items())
                            ])
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
                    prompt_details = getattr(chunk.usage, "prompt_tokens_details", None)
                    cached_tokens = getattr(prompt_details, "cached_tokens", 0) or 0
                    total_cost = getattr(chunk.usage, "cost", 0.0) or 0.0
        except ResponseAborted:
            raise
        except Exception as exc:
            # If cancel_event was set while we were blocked on next(stream),
            # the daemon _stream_closer_thread's stream.close() unblocked us
            # with an exception (typically httpx.StreamClosed).  Treat it as
            # a normal cancellation rather than a real API error.
            if cancel_event is not None and cancel_event.is_set():
                logger.info("Aborted streamed response from %s", resolved_model)
                raise ResponseAborted() from exc
            _log_api_error_context(
                exc,
                resolved_model=resolved_model,
                message_count=len(api_messages),
                total_content_chars=total_content_chars,
                has_tools=bool(tools),
                has_reasoning=has_reasoning,
            )
            raise
        finally:
            # Ensure the cancel thread exits and gets gc'd.
            _stream_done.set()
        content = "".join(content_parts)
        tool_calls = [
            ToolCallRequest(
                id=accumulator["id"], name=accumulator["name"], arguments=accumulator["arguments"])
            for _, accumulator in sorted(tool_call_accumulators.items())
        ] or None
        # Log usage stats including cache hit information
        cache_pct = ((100.0 * cached_tokens) / prompt_tokens) if prompt_tokens > 0 else 0.0
        if cached_tokens > 0:
            logger.info(
                "Usage for %s: prompt_tokens=%d, cached_tokens=%d (%.1f%%), "
                "completion_tokens=%d",
                resolved_model, prompt_tokens, cached_tokens, cache_pct,
                completion_tokens,
            )
        else:
            logger.debug(
                "Received %d-character response from %s (finish_reason=%s, "
                "completion_tokens=%d, prompt_tokens=%d, tool_calls=%s)",
                len(content), resolved_model, finish_reason, completion_tokens, prompt_tokens,
                [call.name for call in tool_calls] if tool_calls else None,
            )
        reply = Message(
            content=content,
            role="assistant",
            num_tokens=estimate_tokens(content),
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason=finish_reason,
            tool_calls=tool_calls,
        )
        return ProviderResponse(
            message=reply, prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens, cached_tokens=cached_tokens,
            total_cost=total_cost,
        )

    @staticmethod
    def _apply_explicit_cache_control(
        messages: list[ChatCompletionMessageParam],
    ) -> list[ChatCompletionMessageParam]:
        """Add cache_control with type ephemeral to every content block in each message.

        This implements Anthropic's 'explicit' prompt caching strategy, where each
        content block is individually tagged for caching.
        """
        result: list[ChatCompletionMessageParam] = []
        for msg in messages:
            msg_copy = dict(msg)
            content = msg_copy.get("content")
            if isinstance(content, str):
                # Wrap string content in a content block and add cache_control
                msg_copy["content"] = [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]
            elif isinstance(content, list):
                # Add cache_control to each existing content block
                new_content = []
                for block in content:
                    if isinstance(block, dict):
                        block_copy = dict(block)
                        block_copy["cache_control"] = {"type": "ephemeral"}
                        new_content.append(block_copy)
                    else:
                        new_content.append(block)
                msg_copy["content"] = new_content
            result.append(cast(ChatCompletionMessageParam, msg_copy))
        return result

    @staticmethod
    def _build_api_messages(
        messages: list[Message], system_prompt: str | None, drop_reasoning: bool
    ) -> list[ChatCompletionMessageParam]:
        """Convert session history (plus an optional system prompt) into OpenAI SDK message
        dicts. `"tool_defs"`- and `"system"`-role messages are omitted: tool definitions are
        offered via the request's separate `tools` array (see `send_prompt`) rather than as a
        chat message, and a stored `"system"` message is only `klorb.session.Session`'s
        bookkeeping record of what was sent on the first turn (see
        `Session._ensure_system_message`) — the live system prompt is supplied fresh via the
        `system_prompt` argument below instead, so it reflects the current active model even
        if it's changed since that bookkeeping message was inserted. `"tool_use"` and
        `"tool_response"` are translated into the shapes the API expects for a tool-calling
        round trip: an `assistant` message carrying `tool_calls`, and a `tool` message
        carrying `tool_call_id`, respectively.

        A `"thinking"`-role message is never sent as its own chat message -- the OpenAI-
        compatible API has no such role. `Session` always appends one immediately ahead of
        the `"assistant"`/`"tool_use"` reply it belongs to (see `Session._send_and_receive`),
        so when `drop_reasoning` is `False` (the default), its `content` and
        `reasoning_details` are instead folded onto that next reply's own request dict as
        `reasoning`/`reasoning_details` fields -- the shape OpenRouter expects for replayed
        reasoning, letting the model continue from its own prior reasoning trace. When
        `drop_reasoning` is `True`, a `"thinking"` message's content is discarded entirely.
        """
        api_messages: list[ChatCompletionMessageParam] = []
        if system_prompt is not None:
            system_message = {"role": "system", "content": system_prompt}
            api_messages.append(cast(ChatCompletionMessageParam, system_message))
        pending_reasoning: Message | None = None
        for message in messages:
            if message.role == "thinking":
                if not drop_reasoning:
                    pending_reasoning = message
                continue
            if message.role in ("tool_defs", "system"):
                continue
            api_message: dict[str, Any]
            if message.role == "tool_use":
                api_message = {"role": "assistant", "content": message.content or None}
                if message.tool_calls:
                    api_message["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                        for call in message.tool_calls
                    ]
            elif message.role == "tool_response":
                api_messages.append(cast(ChatCompletionMessageParam, {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message.content,
                }))
                continue
            elif message.role == "assistant":
                api_message = {"role": "assistant", "content": message.content}
            else:
                api_messages.append(
                    cast(ChatCompletionMessageParam, {"role": message.role, "content": message.content}))
                continue
            if pending_reasoning is not None:
                if pending_reasoning.content:
                    api_message["reasoning"] = pending_reasoning.content
                if pending_reasoning.reasoning_details:
                    api_message["reasoning_details"] = pending_reasoning.reasoning_details

                pending_reasoning = None
            api_messages.append(cast(ChatCompletionMessageParam, api_message))
        return api_messages
