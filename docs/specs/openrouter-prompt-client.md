# OpenRouter prompt client

## Summary

klorb sends prompts to LLMs through [OpenRouter](https://openrouter.ai), using the OpenAI
Python SDK pointed at OpenRouter's OpenAI-compatible API endpoint. This is the foundational
mechanism the rest of the harness will build on to talk to models.

## How it works

* `klorb.api_provider.ApiProvider` (`klorb/src/klorb/api_provider.py`) is an abstract base
  class defining the interface every LLM API provider implements: `get_api_key()`,
  `build_client()`, and `send_prompt(messages, system_prompt, model, session_id)`. This lets
  the library support additional providers later behind a common interface.
  * `klorb.api_provider.ProviderResponse` is the return type of `send_prompt()`: a
    `pydantic.BaseModel` with `message: [[message-model]]` (the assistant's reply — its
    `num_tokens` is the response's completion-token count, `finish_reason` is the
    provider's stop reason) and `prompt_tokens: int` (total input tokens billed for the
    request, used by [[session-and-turns]] to derive the user turn's `num_tokens`).
* `klorb.openrouter.OpenRouterApiProvider` (`klorb/src/klorb/openrouter.py`) implements
  `ApiProvider` for OpenRouter, using the OpenAI SDK. It has no CLI or UI dependencies, so
  it can be instantiated directly by the CLI, the VSCode plugin, or any future caller.
  * `get_api_key()` returns the key passed to the constructor, or falls back to the
    `OPENROUTER_API_KEY` environment variable, raising `RuntimeError` if neither is set.
  * `build_client()` lazily constructs (and caches) an `openai.OpenAI` client with
    `base_url` set to `https://openrouter.ai/api/v1` and the OpenRouter API key.
  * `send_prompt(messages, system_prompt=None, model=None, session_id=None)` builds the
    OpenAI SDK message list from `messages` (a `list[Message]`, converted to `{"role":
    ..., "content": ...}` dicts) with `system_prompt`, if given, prepended as a `"system"`
    role message, sends it to the given model (default `openai/gpt-4o-mini`, an OpenRouter
    model identifier), and returns a `ProviderResponse` built from the first response
    choice's content/`finish_reason` and the response's `usage.completion_tokens`/
    `usage.prompt_tokens`.
* `klorb.cli` (`klorb/src/klorb/cli.py`) is the CLI entry point, registered as the `klorb`
  console script in `pyproject.toml`. It loads `.env` via `python-dotenv`, parses a
  `-m`/`--message` flag holding the prompt and an optional `--model` flag, constructs an
  `OpenRouterApiProvider`, and hands both to a [[session-and-turns]] `Session`. For a
  one-shot prompt it calls `Session.run_one_shot(prompt)` (which calls `send_prompt`
  internally) and prints the result to stdout.

## Configuration

* `OPENROUTER_API_KEY` must be set in the environment (or in a `.env` file in the working
  directory) before sending a prompt.

## Usage

```
klorb -m "What is the capital of France?"
klorb --model anthropic/claude-3.5-sonnet --message "Summarize this repo."
```

## Out of scope

* Streaming responses and tool/function calling are not implemented yet — `send_prompt()`
  always sends the full history it's given and returns one complete reply. Model selection
  beyond a single `--model` flag is not implemented yet.
