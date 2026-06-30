# OpenRouter prompt client

## Summary

klorb sends prompts to LLMs through [OpenRouter](https://openrouter.ai), using the OpenAI
Python SDK pointed at OpenRouter's OpenAI-compatible API endpoint. This is the foundational
mechanism the rest of the harness will build on to talk to models.

## How it works

* `klorb.api_provider.ApiProvider` (`klorb/src/klorb/api_provider.py`) is an abstract base
  class defining the interface every LLM API provider implements: `get_api_key()`,
  `build_client()`, and `send_prompt(prompt, model)`. This lets the library support
  additional providers later behind a common interface.
* `klorb.openrouter.OpenRouterApiProvider` (`klorb/src/klorb/openrouter.py`) implements
  `ApiProvider` for OpenRouter, using the OpenAI SDK. It has no CLI or UI dependencies, so
  it can be instantiated directly by the CLI, the VSCode plugin, or any future caller.
  * `get_api_key()` returns the key passed to the constructor, or falls back to the
    `OPENROUTER_API_KEY` environment variable, raising `RuntimeError` if neither is set.
  * `build_client()` lazily constructs (and caches) an `openai.OpenAI` client with
    `base_url` set to `https://openrouter.ai/api/v1` and the OpenRouter API key.
  * `send_prompt(prompt, model)` sends a single user-role message to the given model
    (default `openai/gpt-4o-mini`, an OpenRouter model identifier) and returns the text of
    the first response choice.
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

* Streaming responses, multi-turn conversation history, tool/function calling, and model
  selection beyond a single `--model` flag are not implemented yet. This spec covers only
  the single-shot prompt-in/text-out path.
