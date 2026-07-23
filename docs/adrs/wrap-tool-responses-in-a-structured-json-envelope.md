# Every `tool_response` message carries a structured JSON envelope, not a bare-result-or-`"Error: ..."` string

* Date: 2026-07-23 00:00
* Question: `SessionToolExecutionMixin._run_tool_calls`'s `_format_tool_response_content(result,
  error)` turned every tool call's outcome into `f"Error: {error}"` on failure, or the bare
  result (as-is if already a string, else JSON-encoded) on success. This gives the model no
  structured way to distinguish a malformed-call mistake it should fix and retry from a
  permission denial it shouldn't retry at all from a transient network hiccup that might
  succeed on retry — it has to guess from the prose. Tools also signalled failure three
  incompatible ways (raising, a result dict with ad hoc `"success"`/`"failure_reason"` fields
  only `BashTool` exposed via an `is_success()` override, or a result dict with a bare `"error"`
  key only `WebFetchTool` used and no `is_success()` override ever saw). Should `tool_response`
  content gain a uniform structure, and if so, what does a tool whose failure carries useful
  partial data (e.g. a failed shell command's `stdout`/`stderr`) do under it?
* Answer: `klorb.tools.response_envelope.ToolResponseEnvelope` (a frozen pydantic `BaseModel`)
  is the JSON object every `tool_response` `Message.content` is serialized from now, built via
  `.success(response_body, ...)` or `.error(message, *, category, response_body=None, ...)` and
  serialized with `.to_wire_dict()` (`model_dump(exclude_none=True)`, additionally dropping
  `system_interjections`/`user_interjections` entirely when empty). Fields: `is_error`,
  `is_retryable` (always derived from `error_category`, never set directly — `False`
  unconditionally on success, so an empty-but-successful result like a no-match `Grep` doesn't
  read as something to retry), `error_category` (a `klorb.tools.exceptions.ErrorCategory`:
  `"transient"`/`"syntax"`/`"validation"`/`"permission"`/`"business_logic"`, mirroring
  Anthropic's own MCP structured-tool-error-response guidance), `error_message`, `response_body`,
  and `system_interjections`/`user_interjections` (see below). Any tool can raise
  `klorb.tools.exceptions.ToolCallError(message, category=..., response_body=...)` to signal a
  categorized failure without inventing its own ad hoc result-dict shape; every catch site
  (first-attempt or retried-after-permission-decision) routes a caught exception through the
  single `klorb.tools.response_envelope.classify_exception()` dispatch table, so categorization
  can't drift between call sites or silently regress to unclassified on a retry path.

  `BashTool` is the one deliberate exception to "`response_body` is `None` when `is_error` is
  `True`": it keeps its existing, non-raising contract (a failed shell command's `apply()`
  returns a result dict with `"success": False`, never raises), and `_run_tool_calls` derives
  `business_logic` categorization one layer up, from the same `tool.is_success(args, result,
  error)` call it already made for `SessionStatistics.tools` bookkeeping. The resulting envelope
  has `is_error=True`, `error_message=None` (the detail already lives in
  `response_body["failure_reason"]`), but `response_body` still carries the tool's own result —
  so a failed command's `stdout`/`stderr` survive instead of being discarded the way every other
  tool's failure discards `response_body`.

  `system_interjections` (a list of `{subject, body}` pairs) is the JSON-delivered counterpart
  to the XML `<SystemInterjection subject="...">` block a user-turn prompt carries: every
  registered `_standing_interjection_providers` entry is polled once per `_run_tool_calls` call
  (once per tool-call round, not once per individual call within it), and whichever providers
  returned a message are attached to the first envelope built in that round. `user_interjections`
  is reserved on the schema, always empty, for a future plan to populate with queued user
  messages delivered mid-tool-loop.
* Reasoning: A flat `error_category` enum lets the model make a retry-or-not decision without
  parsing prose, and gives every tool one shared vocabulary for "why did this fail" instead of
  three incompatible ad hoc shapes. Keeping `is_retryable` derived (never settable directly) means
  a tool author can't accidentally mark an unclassified failure retryable, or a successful-but-
  empty result as something worth retrying — both are footguns a hand-set boolean invites and a
  derived one forecloses entirely.

  The `BashTool` deviation was considered against the alternative of making `_execute`/
  `_execute_persistent` raise `ToolCallError` on a non-zero exit instead, which would satisfy
  "`response_body` is always `None` on error" without exception — but at the cost of splitting
  `BashTool.summary()`/`detail_view()` into two different data sources (the exception's payload
  for a failed call, `result` for a successful one), removing its one existing `is_success()`
  override for no benefit, and touching three separate result-construction call sites
  (`_execute`, `_execute_persistent`, `_sandbox_stale_response`) to convert a `return` into a
  `raise`. Preserving `stdout`/`stderr` on a failed shell command — arguably the single most
  useful part of a failed command's response — was judged worth the one documented exception to
  the envelope's own "`response_body` is `None` on error" contract, rather than rewriting
  `BashTool`'s internals to avoid having an exception to document at all.

  `system_interjections` riding along on a `tool_response` message, rather than only ever
  appearing as an XML block on the next user-turn prompt, closes a real gap: `_dispatch_turn`'s
  `while reply.role == "tool_use"` loop can drive many `_run_tool_calls`/`_send_and_receive`
  round trips before the model stops asking for tools, and a standing reminder (e.g.
  `TodoNextTool`'s current-task nudge) was invisible for the rest of that turn once the
  triggering user prompt scrolled out of recent context — exactly the deep-tool-loop case where
  the model most needs reminding. See
  [the standing-interjections ADR](standing-interjections-complement-one-shot-for-level-triggered-state.md)
  for the registry itself, which this plan reuses unchanged — it now just has two delivery
  mechanisms (XML onto a user prompt, JSON onto a tool_response's first envelope in a round)
  instead of one.

  `ErrorCategory`/`ToolCallError` live in `klorb.tools.exceptions` rather than
  `klorb.tools.response_envelope`, so that module keeps its existing role as a dependency-light
  leaf every tool can import without pulling in pydantic-model machinery, and
  `response_envelope.py` depends on `exceptions.py` in one direction only.
