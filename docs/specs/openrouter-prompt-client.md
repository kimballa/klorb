# OpenRouter prompt client

## Summary

klorb sends prompts to LLMs through [OpenRouter](https://openrouter.ai), using the OpenAI
Python SDK pointed at OpenRouter's OpenAI-compatible API endpoint. This is the foundational
mechanism the rest of the harness will build on to talk to models.

## How it works

* `klorb.api_provider.ApiProvider` (`klorb/src/klorb/api_provider.py`) is an abstract base
  class defining the interface every LLM API provider implements: `get_api_key()`,
  `build_client()`, and `send_prompt(messages, system_prompt, model, session_id, on_chunk)`.
  This lets the library support additional providers later behind a common interface.
  * `klorb.api_provider.ProviderResponse` is the return type of `send_prompt()`: a
    `pydantic.BaseModel` with `message: [[message-model]]` (the assistant's reply — its
    `num_tokens` is the response's completion-token count, `finish_reason` is the
    provider's stop reason) and `prompt_tokens: int` (total input tokens billed for the
    request, used by [[session-and-turns]] to derive the user turn's `num_tokens`).
  * `on_chunk: Callable[[str], None] | None` is invoked once per non-empty text delta as
    the response streams in; the final `ProviderResponse` is still returned once the stream
    completes, so callers that don't pass `on_chunk` see no difference in behavior.
* `klorb.openrouter.OpenRouterApiProvider` (`klorb/src/klorb/openrouter.py`) implements
  `ApiProvider` for OpenRouter, using the OpenAI SDK. It has no CLI or UI dependencies, so
  it can be instantiated directly by the CLI, the VSCode plugin, or any future caller.
  * `get_api_key()` returns the key passed to the constructor, or falls back to the
    `OPENROUTER_API_KEY` environment variable, raising `RuntimeError` if neither is set.
  * `build_client()` lazily constructs (and caches) an `openai.OpenAI` client with
    `base_url` set to `https://openrouter.ai/api/v1` and the OpenRouter API key.
  * `send_prompt(messages, system_prompt=None, model=None, session_id=None, on_chunk=None)`
    builds the OpenAI SDK message list from `messages` (a `list[Message]`, converted to
    `{"role": ..., "content": ...}` dicts) with `system_prompt`, if given, prepended as a
    `"system"` role message, and always calls the OpenAI SDK's streaming completions API
    (`stream=True`, `stream_options={"include_usage": True}`) against the given model
    (default `openai/gpt-4o-mini`, an OpenRouter model identifier). It iterates the
    resulting `Stream[ChatCompletionChunk]`, calling `on_chunk` per non-empty
    `choices[0].delta.content`, tracking `choices[0].finish_reason` whichever chunk carries
    it, and reading `chunk.usage.completion_tokens`/`prompt_tokens` from whichever chunk
    carries it (the chunk carrying `usage` can have an empty `choices` list, so that's
    guarded separately). Once the stream ends, it returns one `ProviderResponse` built from
    the joined content and those tracked values.
* `klorb.cli` (`klorb/src/klorb/cli.py`) is the CLI entry point, registered as the `klorb`
  console script in `pyproject.toml`. It loads `.env` via `python-dotenv`, parses a
  `-m`/`--message` flag holding the prompt and an optional `--model` flag, constructs an
  `OpenRouterApiProvider`, and hands both to a [[session-and-turns]] `Session`. For a
  one-shot prompt it calls `Session.run_one_shot(prompt, on_chunk=...)` with a callback
  that prints each chunk as it arrives (`end="", flush=True`, no buffering), then prints a
  single trailing newline — a typewriter effect on stdout. If nothing ever streamed (e.g. a
  mocked `Session` in a test), it falls back to printing the full response once instead.

## Configuration

* `OPENROUTER_API_KEY` must be set in the environment (or in a `.env` file in the working
  directory) before sending a prompt.

## Usage

```
klorb -m "What is the capital of France?"
klorb --model anthropic/claude-3.5-sonnet --message "Summarize this repo."
```

## Out of scope

* Tool/function calling is not implemented yet. Model selection beyond a single `--model`
  flag is not implemented yet.
