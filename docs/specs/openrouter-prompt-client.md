# OpenRouter prompt client

## Summary

klorb sends prompts to LLMs through [OpenRouter](https://openrouter.ai), using the OpenAI
Python SDK pointed at OpenRouter's OpenAI-compatible API endpoint. This is the foundational
mechanism the rest of the harness will build on to talk to models.

## How it works

* `klorb.openrouter` (`klorb/src/klorb/openrouter.py`) is the library module. It has no CLI
  or UI dependencies, so it can be called directly by the CLI, the VSCode plugin, or any
  future caller.
  * `get_api_key()` reads the `OPENROUTER_API_KEY` environment variable and raises
    `RuntimeError` if it isn't set.
  * `build_client()` constructs an `openai.OpenAI` client with `base_url` set to
    `https://openrouter.ai/api/v1` and the OpenRouter API key.
  * `send_prompt(prompt, model, client)` sends a single user-role message to the given
    model (default `openai/gpt-4o-mini`, an OpenRouter model identifier) and returns the
    text of the first response choice.
* `klorb.cli` (`klorb/src/klorb/cli.py`) is the CLI entry point, registered as the `klorb`
  console script in `pyproject.toml`. It loads `.env` via `python-dotenv`, parses a single
  positional `prompt` argument and an optional `--model` flag, calls `send_prompt`, and
  prints the result to stdout.

## Configuration

* `OPENROUTER_API_KEY` must be set in the environment (or in a `.env` file in the working
  directory) before sending a prompt.

## Usage

```
klorb "What is the capital of France?"
klorb --model anthropic/claude-3.5-sonnet "Summarize this repo."
```

## Out of scope

* Streaming responses, multi-turn conversation history, tool/function calling, and model
  selection beyond a single `--model` flag are not implemented yet. This spec covers only
  the single-shot prompt-in/text-out path.
