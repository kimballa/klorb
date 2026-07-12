# Configure the tiktoken cache env var after the REPL app mounts, not before it starts

* Date: 2026-07-12 19:00
* Question: `klorb.token_estimate.configure_tiktoken_cache_env()` logs an INFO line
  ("Found bundled tiktoken cache at ...") when it points tiktoken at the `klorb
  init`-installed cache. For an interactive session, should this still be called from
  `klorb.cli.main()`, right after `configure_logging()` and before `run_repl()` starts the
  `ReplApp`, as it was originally?
* Answer: No. For an interactive session, `klorb.tui.repl.ReplApp.on_mount()` calls it
  instead, as its first statement -- once the Textual app is actually running. `klorb.cli.
  main()` still calls it directly for a one-shot prompt, immediately after `configure_logging()`,
  since a one-shot invocation has no Textual app to wait for.
* Reasoning: In `repl_mode`, `configure_logging()` attaches a `textual.logging.TextualHandler`.
  `TextualHandler.emit()` routes a log record through the active Textual app's own log
  (`app.log.logging(...)`) when one is running, but falls back to printing straight to stderr
  when `textual._context.active_app` has no value set -- which is exactly the state during the
  window between `configure_logging()` returning and `ReplApp.run()` actually starting the app.
  A `FileHandler` for the session log file is attached at the same time as the `TextualHandler`
  (both come out of the same `configure_logging()` call), so a log record emitted in that window
  still reaches the session log file correctly; the problem was cosmetic but user-visible: the
  cache-cache line (and anything else logged in that window) printed directly onto the user's
  real terminal for an instant, immediately before the TUI cleared the screen and took it over,
  even though the same content already landed in the file. Moving the call to `on_mount()`
  waits until the app is mounted (so `active_app` is set), meaning the log line only ever
  reaches the app's own log and the session log file -- never a raw stderr flash. The timing
  is still early enough for its purpose: nothing that estimates token counts (`Session.
  _ensure_system_message()`/`_ensure_tool_defs_message()`, the first things to call `klorb.
  token_estimate.estimate_tokens()`) runs before the first turn is dispatched, well after
  `on_mount()` completes.
