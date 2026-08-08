# /clear starts a new Session (and log file), not just an empty history

* Date: 2026-06-30 00:00
* Question: When the user types `/clear` in the REPL, should `ReplApp` reset the existing
  `Session`'s message list in place, or replace `self._session` with a brand-new `Session`
  (new id, new log file)?
* Answer: Replace it. `ReplApp` constructs a new `Session` with the same `SessionConfig`
  (model carries over) and the same underlying `ApiProvider`/`ModelRegistry` (via
  `Session.provider`/`Session.model_registry` read-only properties, so the OpenAI client
  and model discovery aren't rebuilt), but a fresh `generate_session_id()`. If session
  logging was enabled for this REPL invocation (threaded in from `cli.py` as
  `ReplApp(session_log_enabled=...)`/`run_repl(session_log_enabled=...)`), logging is
  reconfigured to a new per-session log file for the new id via
  `configure_logging()`/`session_log_path()`, relying on `configure_logging`'s existing
  `force=True` behavior to safely repoint the root logger mid-process. The visible history
  widget's children are removed. This all happens synchronously in `on_input_submitted`
  (no worker thread, no disabling the input box), since no model call is involved.
* Reasoning: A session id is klorb's unit of "one conversation" for both logging and
  message history — clearing the history without rotating the id would leave the old log
  file silently continuing to receive log lines for what the user now considers a new
  conversation, and would leave `Session.id` pointing at a session whose visible history no
  longer matches its log. Reusing the same provider/registry instances avoids rebuilding
  them (a real HTTP client and a package scan) on every `/clear`, and avoids silently
  dropping a caller-supplied provider (e.g. a test's mock).
