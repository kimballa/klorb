# Route one-shot and REPL prompts through a shared Session

* Date: 2026-06-30 00:00
* Question: Should the one-shot prompt path keep calling `ApiProvider.send_prompt()`
  directly while the REPL keeps its own separate provider/model state, or should both go
  through one shared object?
* Answer: Both now go through `klorb.session.Session`. `klorb.cli.main()` builds a single
  `SessionConfig`/`Session` and uses it for either path: `session.run_one_shot(prompt)` for
  a one-shot prompt, or `run_repl(session, initial_message=prompt)` for the REPL.
  `ReplApp` no longer takes a raw `ApiProvider`/model pair — it takes a `Session` and calls
  `session.send_turn()` per submitted prompt.
* Reasoning: The CLI's `-m`/`--message` flag and the new `--interactive` flag mean a single
  invocation can need to send a prompt *and* potentially stay in the REPL afterward —
  there's no longer a clean split between "one-shot path" and "REPL path" at the call site.
  A shared `Session` gives both paths the same turn-sending logic (`send_turn()`, which
  resolves the active model via `ModelRegistry` before calling `ApiProvider.send_prompt()`)
  so that logic isn't duplicated, and gives future per-session behavior (history, tool
  calls) one place to live rather than two. `Session` itself stays UI-agnostic (no Textual
  imports), so it can also be reused by future non-CLI callers (e.g. the VSCode plugin).
