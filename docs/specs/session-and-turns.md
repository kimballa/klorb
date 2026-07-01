# Session and turns

## Summary

A `Session` holds the state for klorb's active interaction with a model — its
`SessionConfig`, the `ApiProvider` it sends prompts through, and the `ModelRegistry` it
resolves model names against — and exposes the turn-based loop that both the one-shot
prompt path and the interactive REPL send their prompts through. This is framework-level:
the CLI and the REPL no longer talk to an `ApiProvider` directly, they both go through a
`Session`, so any future per-session behavior (conversation history, tool calls, persisted
config) has one place to live.

## How it works

* `klorb.session.SessionConfig` (`klorb/src/klorb/session.py`) is a `pydantic.BaseModel`
  holding the config a `Session` is constructed from:
  * `model: str` — the configured model identifier, defaulting to
    `klorb.openrouter.DEFAULT_MODEL`.
  * `interactive: bool` — whether the session stays in the REPL after its first turn,
    defaulting to `True`.
  * `thinking_enabled: bool` — whether extended-thinking is requested for turns sent to a
    thinking-capable model, defaulting to `True`.
  * `thinking_effort: ThinkingEffort` (`"low" | "medium" | "high"`) — the user-facing
    reasoning depth knob, defaulting to `"high"`. See [[default-thinking-on-and-high-effort]]
    for why these are the defaults.
* `klorb.session.Session` is constructed with a `SessionConfig` (saved as `self.config`),
  and optionally an `ApiProvider` (defaults to a fresh `OpenRouterApiProvider`) and a
  `ModelRegistry` (defaults to a fresh `ModelRegistry()`). It also holds `self._messages:
  list[[[message-model]]]`, exposed read-only via the `messages` property, plus read-only
  `provider`/`model_registry` properties so callers (e.g. [[terminal-repl]]'s `/clear`) can
  build a new `Session` that reuses the same provider/registry instances.
  * `active_model_name() -> str` resolves `config.model` against the `ModelRegistry`: if
    it's a registered [[model-framework]] `Model`, its `name()` is returned (invoking the
    `Model`); otherwise `config.model` is returned unchanged, so an arbitrary OpenRouter
    model identifier still works even without a `Model` implementation.
  * `_reasoning_params() -> dict[str, Any] | None` builds the `reasoning` body passed to
    `ApiProvider.send_prompt()`: `None` if `config.thinking_enabled` is `False`, the active
    model isn't registered, or its `capabilities()["thinking"]` isn't `True`; otherwise
    `{"effort": config.thinking_effort}` or `{"max_tokens": <budget>}` depending on the
    model's `capabilities()["thinking_budget_style"]` (defaulting to `"effort"` if the
    model omits the key), with the token budget looked up from
    `THINKING_EFFORT_TOKEN_BUDGETS[config.thinking_effort]` (see
    [[map-thinking-effort-levels-to-fixed-token-budgets]]). Recomputed fresh on every turn,
    like `system_prompt`, so palette changes to thinking config take effect immediately.
  * `send_turn(prompt: str, on_chunk: ... = None, on_thinking_chunk: ... = None) -> str` is
    the turn-based loop's unit of work. It appends a new `Message` (`role="user"`,
    `processing_state="pending"`) to the history, then sends the *entire* history plus the
    active model's system prompt (re-derived fresh from `Model.system_prompt()` on every
    call — not itself stored as a `Message`) and `_reasoning_params()`'s result via
    `ApiProvider.send_prompt(messages, system_prompt=, model=, session_id=, reasoning=,
    on_chunk=, on_thinking_chunk=)`. As chunks arrive, `Session` (not the provider) owns the
    in-progress assistant `Message`: on the first content chunk it appends a placeholder
    (`processing_state="started_receipt"`, `streaming_content=[]`), appends each subsequent
    chunk to it, and forwards each chunk to the caller's own `on_chunk` if given (see
    [[session-owns-in-progress-assistant-message-during-streaming]]). Reasoning/thinking
    chunks are handled the same way but into a separate `role="thinking"` placeholder
    appended ahead of the assistant one (so streamed reasoning, which typically arrives
    first, and the eventual answer can be told apart), forwarded to `on_thinking_chunk`.
    On success, both placeholders are finalized in place (`content`, `streaming_content=
    None`, `processing_state="complete"`) — the assistant one also gets `num_tokens` and
    `finish_reason` from the response, while the thinking one's `num_tokens` stays `0`
    since reasoning tokens are already folded into the assistant message's count — or, for
    the assistant message, the provider's reply is appended fresh if no placeholder was
    ever created (e.g. a non-streaming test double; there's no non-streaming fallback for
    thinking, since without streaming there's nothing to have generated a thinking
    placeholder from). The user `Message` is mutated in place — `num_tokens` via a delta
    against the response's `prompt_tokens` (see
    [[derive-user-turn-token-counts-from-a-prompt-token-delta]]), `processing_state`
    becomes `"complete"`, `last_error` is cleared. On failure, it mutates the user `Message`
    to `processing_state="error"`/`last_error=str(exc)` (and both placeholders too, if
    created, leaving their partial `streaming_content` intact) and re-raises. Both a
    one-shot prompt and every REPL submission are exactly one call to `send_turn()`.
  * `retry_last_turn(on_chunk: ... = None, on_thinking_chunk: ... = None) -> str` scans
    backward for the last `role == "user"` `Message`; if it isn't in `"error"` state (or
    none exists), raises `ValueError`. Otherwise it discards everything after that message
    (e.g. a partial assistant placeholder left by a failed stream) and resends its content,
    mutating that same `Message` again rather than appending a duplicate (see
    [[mutate-the-same-message-on-turn-error-and-retry]] and
    [[session-owns-in-progress-assistant-message-during-streaming]]). Not yet wired into
    the TUI/CLI.
  * `run_one_shot(prompt: str, on_chunk: ... = None, on_thinking_chunk: ... = None) -> str`
    is a thin wrapper around `send_turn()`, named for the non-interactive entry point so
    callers don't need to know it's "just" a single turn.
* `klorb.cli.main()` (`klorb/src/klorb/cli.py`) builds a `SessionConfig` from parsed CLI
  arguments (`--model`, the resolved `--interactive` value, and the log path returned by
  `configure_logging()`), constructs a `Session`, and either calls
  `session.run_one_shot(prompt)` and prints the result, or calls
  [[terminal-repl]]'s `run_repl(session, initial_message=prompt)` to start the REPL.
* `klorb.tui.repl.ReplApp` (`klorb/src/klorb/tui/repl.py`) takes a `Session` instead of a
  raw provider/model pair. Every submitted prompt — typed by the user or passed in as
  `initial_message` — goes through `Session.send_turn()` on a background worker thread.
  Selecting a model from the command palette ([[model-framework]]) sets
  `session.config.model` directly.
* `--interactive` / `--no-interactive` (`klorb.cli.build_parser()`) is an
  `argparse.BooleanOptionalAction` defaulting to `None`. `klorb.cli.main()` resolves it to a
  bool: if the flag wasn't given explicitly, the session is interactive exactly when no
  `-m`/`--message` was given (preserving the original "no message → REPL, message → one-shot"
  default); an explicit `--interactive`/`--no-interactive` overrides that default in either
  direction. Passing `-m`/`--message` together with an explicit `--interactive` starts the
  REPL with that message auto-submitted as the first turn (see [[terminal-repl]]), so the
  user's starting message behaves exactly as if they'd typed it themselves, and the REPL
  stays open afterward. Passing `--no-interactive` without `-m`/`--message` is a usage error
  (there would be nothing to send).

## Usage

```
klorb -m "What is 2+2?"                  # one-shot: Session.run_one_shot, no REPL
klorb -m "What is 2+2?" --interactive    # REPL, with the message as the first turn
klorb --no-interactive                   # error: nothing to send
klorb                                    # REPL, no initial message (default)
```

## Out of scope

* The system prompt's token cost is folded into the first user turn's `num_tokens` delta
  rather than tracked as its own `Message` — an intentional v1 approximation, since there's
  no per-message tokenizer to attribute tokens precisely without a round trip to the model.
* `retry_last_turn()` exists on `Session` but isn't yet exposed through the TUI or CLI.
* Tool/function calling and persisting `SessionConfig` across invocations are not
  implemented yet.
* `thinking_enabled`/`thinking_effort` have no `--thinking`/`--thinking-effort` CLI flags
  yet; they're only reachable via [[terminal-repl]]'s command palette, so a one-shot
  `klorb -m ...` invocation always uses the `SessionConfig` defaults (thinking on, `"high"`
  effort).
