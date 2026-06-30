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
  * `log_filename: str | None` — the session log file's path (as written by
    [[paths-and-logging]]'s `configure_logging()`), or `None` if no session log was
    written.
* `klorb.session.Session` is constructed with a `SessionConfig` (saved as `self.config`),
  and optionally an `ApiProvider` (defaults to a fresh `OpenRouterApiProvider`) and a
  `ModelRegistry` (defaults to a fresh `ModelRegistry()`).
  * `active_model_name() -> str` resolves `config.model` against the `ModelRegistry`: if
    it's a registered [[model-framework]] `Model`, its `name()` is returned (invoking the
    `Model`); otherwise `config.model` is returned unchanged, so an arbitrary OpenRouter
    model identifier still works even without a `Model` implementation.
  * `send_turn(prompt: str) -> str` is the turn-based loop's unit of work: it resolves
    the active model name and calls `ApiProvider.send_prompt(prompt, model=...)`, returning
    the text response. Both a one-shot prompt and every REPL submission are exactly one
    call to `send_turn()`.
  * `run_one_shot(prompt: str) -> str` is a thin wrapper around `send_turn()`, named for the
    non-interactive entry point so callers don't need to know it's "just" a single turn.
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

* `Session` does not maintain multi-turn conversation history to send back to the model —
  each `send_turn()` is still an independent, stateless `send_prompt` call, matching
  [[terminal-repl]]'s existing out-of-scope note.
* Tool/function calling and persisting `SessionConfig` across invocations are not
  implemented yet.
