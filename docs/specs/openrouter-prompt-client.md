# OpenRouter prompt client

## Summary

klorb sends prompts to LLMs through [OpenRouter](https://openrouter.ai), using the OpenAI
Python SDK pointed at OpenRouter's OpenAI-compatible API endpoint. This is the foundational
mechanism the rest of the harness will build on to talk to models.

## How it works

* `klorb.api_provider.ApiProvider` (`klorb/src/klorb/api_provider.py`) is an abstract base
  class defining the interface every LLM API provider implements: `get_api_key()`,
  `build_client()`, and `send_prompt(messages, system_prompt, model, session_id, reasoning,
  tools, on_chunk, on_thinking_chunk)`. This lets the library support additional providers
  later behind a common interface.
  * `klorb.api_provider.ProviderResponse` is the return type of `send_prompt()`: a
    `pydantic.BaseModel` with `message: [[message-model]]` (the assistant's reply — its
    `num_tokens` is the response's completion-token count, `finish_reason` is the
    provider's stop reason, `tool_calls` is populated if the model requested tool calls) and
    `prompt_tokens: int` (total input tokens billed for the request, used by
    [[session-and-turns]] to derive the user turn's `num_tokens`).
  * `tools: list[dict[str, Any]] | None` is the OpenAI-style function-calling `tools` array
    (see [[tool-framework]]'s `ToolRegistry.tool_definitions()`) offered to the model
    alongside the prompt; `None` or empty offers no tools. [[session-and-turns]]'s `Session`
    builds this from its `tool_registry` on every turn — `ApiProvider` implementations just
    forward it and report back whatever tool calls the model requests.
  * `reasoning: dict[str, Any] | None` is a provider-shaped request body asking for
    extended thinking (e.g. `{"effort": "high"}` or `{"max_tokens": 32_768}` for
    OpenRouter); `None` requests no reasoning. [[session-and-turns]]'s `Session` is
    responsible for building this dict from `SessionConfig` and the active model's
    capabilities — `ApiProvider` implementations just forward whatever shape they're given.
  * `on_chunk: Callable[[str], None] | None` is invoked once per non-empty text delta as
    the response streams in; `on_thinking_chunk: Callable[[str], None] | None` is invoked
    once per non-empty reasoning/thinking text delta, separately from `on_chunk`. The final
    `ProviderResponse` is still returned once the stream completes, so callers that don't
    pass either callback see no difference in behavior.
* `klorb.openrouter.OpenRouterApiProvider` (`klorb/src/klorb/openrouter.py`) implements
  `ApiProvider` for OpenRouter, using the OpenAI SDK. It has no CLI or UI dependencies, so
  it can be instantiated directly by the CLI, the VSCode plugin, or any future caller.
  * `get_api_key()` returns the key passed to the constructor, or falls back to the
    `OPENROUTER_API_KEY` environment variable, raising `RuntimeError` if neither is set.
  * `build_client()` lazily constructs (and caches) an `openai.OpenAI` client with
    `base_url` set to `https://openrouter.ai/api/v1` and the OpenRouter API key.
  * `send_prompt(messages, system_prompt=None, model=None, session_id=None, reasoning=None,
    tools=None, on_chunk=None, on_thinking_chunk=None)` builds the OpenAI SDK message list
    from `messages` (a `list[Message]`, converted to `{"role": ..., "content": ...}` dicts,
    dropping any `role="thinking"`/`role="tool_defs"` message and translating
    `role="tool_use"`/`role="tool_response"` — see below) with `system_prompt`, if given,
    prepended as a `"system"` role message, and always calls the OpenAI SDK's streaming
    completions API (`stream=True`, `stream_options={"include_usage": True}`) against the
    given model (default `openai/gpt-4o-mini`, an OpenRouter model identifier). `session_id`
    and `reasoning`, if given, are folded into `extra_body` (as `{"session_id": ...}` and/or
    `{"reasoning": ...}`); `extra_body` stays `None` if neither is given, so requests that
    don't need it are unchanged from before reasoning support was added. `tools`, if
    non-empty, is passed through as the request's `tools` array unchanged. It iterates the
    resulting `Stream[ChatCompletionChunk]`, calling `on_chunk` per non-empty
    `choices[0].delta.content` and `on_thinking_chunk` per non-empty
    `choices[0].delta.reasoning` (an OpenRouter extension field read via `getattr` since the
    OpenAI SDK's `ChoiceDelta` doesn't declare it, but its Pydantic models are configured
    with `extra="allow"` so vendor fields still deserialize and remain accessible),
    tracking `choices[0].finish_reason` whichever chunk carries it, reading
    `chunk.usage.completion_tokens`/`prompt_tokens` from whichever chunk carries it (the
    chunk carrying `usage` can have an empty `choices` list, so that's guarded separately),
    and reassembling any `choices[0].delta.tool_calls` fragments (each chunk during a tool
    call carries a stable `index` per parallel call, with `id`/`function.name` sent once and
    `function.arguments` streamed piecemeal — accumulated here per index, keyed by
    `index`, into complete `ToolCallRequest`s). Once the stream ends, it returns one
    `ProviderResponse` built from the joined content, those tracked values, and the
    reassembled `tool_calls` (`None` if none were requested).
  * `_build_api_messages()` omits any `message.role in ("thinking", "tool_defs")` entries:
    neither is a role the OpenAI-compatible chat completions API accepts — reasoning content
    is treated as ephemeral per-turn output rather than something replayed as conversation
    history on later turns (see [[exclude-thinking-messages-from-outgoing-api-requests]]),
    and tool definitions are offered via the request's separate `tools` array, not a chat
    message (see [[tool-framework]]). `role="tool_use"` becomes an `{"role": "assistant",
    "content": ..., "tool_calls": [...]}` dict (each `ToolCallRequest` reshaped into the
    SDK's `{"id", "type": "function", "function": {"name", "arguments"}}` shape); `role=
    "tool_response"` becomes `{"role": "tool", "tool_call_id": ..., "content": ...}`.
* `klorb.cli` (`klorb/src/klorb/cli.py`) is the CLI entry point, registered as the `klorb`
  console script in `pyproject.toml`. It loads `.env` via `python-dotenv`, parses a
  `-m`/`--message` flag holding the prompt and an optional `--model` flag, constructs an
  `OpenRouterApiProvider` and a [[tool-framework]] `ToolRegistry` (from the loaded
  `ProcessConfig` and the new session's `SessionConfig`), and hands all three to a
  [[session-and-turns]] `Session`. For a one-shot prompt it calls
  `Session.run_one_shot(prompt, on_chunk=...)` with a callback that prints each chunk as it
  arrives (`end="", flush=True`, no buffering), then prints a single trailing newline — a
  typewriter effect on stdout. If nothing ever streamed (e.g. a mocked `Session` in a test),
  it falls back to printing the full response once instead.

## Configuration

* `OPENROUTER_API_KEY` must be set in the environment (or in a `.env` file in the working
  directory) before sending a prompt.

## Usage

```
klorb -m "What is the capital of France?"
klorb --model anthropic/claude-3.5-sonnet --message "Summarize this repo."
```

## Out of scope

* Model selection beyond a single `--model` flag is not implemented yet.
