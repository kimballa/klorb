# Save session state on crash unconditionally (if trusted), no confirm prompt

* Date: 2026-07-12
* Question: Session persistence (docs/specs/session-persistence.md) already lets a trusted
  workspace save `SessionConfig` + message history to `last-session.json` on a normal quit,
  via `ReplApp._quit_after_maybe_saving`'s "Save session state before quitting?" confirm
  prompt. An unhandled exception is a different kind of exit: Textual's own crash handling
  (see docs/adrs/tee-textual-crash-output-to-a-tmp-file.md) prints a traceback and tears the
  app down without ever reaching `action_quit`, so nothing offers to save there today, and the
  conversation is simply gone once the process exits. Should a crash also save session state,
  and if so, should it still ask first the way a normal quit does?
* Answer: `klorb.tui.repl.run_repl()` calls a new `_handle_repl_crash(app, crash_tee)` once
  `App.run()` returns with `app.return_code == 1` (set by `App._handle_exception`). If the
  crashed app's live session's workspace is trusted (`app._session.config.workspace.trusted`
  — the same gate `_quit_after_maybe_saving` checks), it calls `klorb.workspace.last_session.
  write_last_session` unconditionally, with no confirmation prompt, then prints the saved
  path to stderr (or a fallback line if the write itself fails). An untrusted or unresolved
  workspace is skipped entirely, exactly like the normal quit path.
* Reasoning: By the time `_handle_repl_crash` runs, the TUI is already gone — Textual has
  restored the terminal and there is no screen left to push a `ConfirmScreen` onto, so "ask
  first" isn't an available option the way it is for `action_quit`. The only real choice is
  "save unconditionally" vs. "never save on crash," and losing an entire conversation to a
  crash is a strictly worse outcome than writing an unprompted `last-session.json` the user
  didn't explicitly ask for in the moment — especially since restoring it is itself gated
  behind the same workspace-trust check and is never silently substituted for a fresh session
  without the workspace already being trusted. Keeping the trust gate (rather than saving
  regardless of trust) preserves the existing invariant that klorb never writes into a
  workspace's per-project data directory before the user has trusted it, matching `klorb.
  workspace.last_session`'s only other writer.
