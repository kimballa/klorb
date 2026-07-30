# Quiet the ACP SDK's own `RequestError` tracebacks with a logging filter, not a monkeypatch

* Date: 2026-07-29 16:00
* Question: `agent-client-protocol` 0.7.1's `Connection._run_request` re-raises every caught
  `RequestError` after already sending its JSON-RPC error reply on the wire, and the SDK's
  `TaskSupervisor` logs that re-raise via `logging.exception("Background task failed", ...)`
  against the root logger. That means every *expected* protocol-level rejection
  `KlorbAcpAgent` raises (an unknown `sessionId`, a second concurrent prompt, an unsupported
  content block, ...) prints a full traceback to stderr, indistinguishable from a genuine
  unexpected failure. `acp.run_agent()`/`AgentSideConnection` build their `Connection`
  internally and expose no seam to swap in a different task-error handler. How should klorb
  quiet these without patching the SDK?
* Answer: `klorb.logging_config.AcpBackgroundTaskErrorFilter`, a `logging.Filter` installed on
  the root logger by `configure_logging()`, matches the SDK's own hardcoded `"Background task
  failed"` message. When the attached exception is a `RequestError` other than
  `RequestError.internal_error()`, it reports one concise line via klorb's own logger (`INFO`
  for a standard JSON-RPC protocol code -- parse error, invalid request, method not found,
  invalid params -- `WARNING` for anything else, e.g. klorb's own application-level codes like
  "a prompt is already in progress") and suppresses the original record (`filter()` returns
  `False`) so the full traceback never reaches a handler. `RequestError.internal_error()` is
  left alone (the record passes through unfiltered): the SDK raises that specific code when it
  caught some other, non-`RequestError` exception, so it does reflect a genuine bug worth its
  traceback.
* Reasoning: There's no SDK-exposed way to override `TaskSupervisor`'s per-task error handling
  from outside `Connection.__init__`, and subclassing/vendoring `AgentSideConnection` just to
  swap one log call is a much larger surface to keep in sync with the SDK than a filter matching
  one hardcoded string. A `logging.Filter` on the root logger is the narrowest available seam:
  it's inert everywhere else in the process (the TUI and one-shot prompts never produce a
  "Background task failed" record), self-contained to `klorb.logging_config`, and trivial to
  delete if a future SDK version distinguishes these cases itself. The message match is exact
  (not a substring or regex) so an SDK upgrade that changes the wording fails open -- the filter
  stops matching and tracebacks return -- rather than failing closed and silently swallowing
  some new class of exception it was never meant to touch.
