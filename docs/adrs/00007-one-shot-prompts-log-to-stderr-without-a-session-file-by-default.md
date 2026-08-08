# One-shot prompts log to stderr without a session file by default

* Date: 2026-06-29 23:00
* Question: Should a one-shot `klorb <prompt>` invocation configure logging the same way as
  the interactive REPL — `TextualHandler` plus an always-created session log file under
  `KLORB_STATE_DIR/session-logs/`?
* Answer: No. `klorb.logging_config.configure_logging()` now takes `repl_mode` and
  `session_log` flags. A one-shot prompt passes `repl_mode=False`, so logs go straight to
  stderr via a plain `logging.StreamHandler` rather than `TextualHandler`. It also passes
  `session_log=False` by default, so no session log file is written, unless the user passes
  `--session-log`. The REPL keeps the original behavior: `repl_mode=True` (TextualHandler)
  and `session_log=True` by default, with `--no-session-log` available to opt out.
* Reasoning: A one-shot prompt is typically invoked from scripts or piped into other
  commands; `TextualHandler`'s stderr fallback already produced the right destination for
  that case, but routing through `TextualHandler` was a vestige of REPL-oriented code with
  no Textual app to actually attach to. A plain `StreamHandler` is the more direct way to
  say "no TUI here." Writing a session log file on every one-shot invocation also left a
  trail of single-prompt log files under `session-logs/` with no use for most callers, since
  there's no interactive session to debug after the fact; `--session-log` lets a caller opt
  back in when they do want a durable record (e.g. while debugging a script). The REPL still
  defaults to writing a session log, since the interactive session is exactly the kind of
  long-lived run this file format exists to capture; `--no-session-log` covers cases where a
  user doesn't want that.
