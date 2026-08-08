# The bash risk classifier batches via a new `PermissionAskContext.sibling_items` field, not a Session-level call

* Date: 2026-07-11 00:00
* Question: `docs/plans/archive/008-llm-command-risk-scoring.md` (PLAN-008) specifies that
  `classify_command_risk()` must be invoked from `ReplApp._confirm_permission_ask`, immediately
  before `PermissionAskPanel` is shown, and that a compound `BashTool` call's several ask items
  must share a *single* classifier round trip rather than one per item. But
  `Session._resolve_multi_permission_ask` calls `callbacks.on_permission_ask` once per item,
  serially, blocking on each item's decision before moving to the next — so by the time
  `ReplApp` sees the first item's `PermissionAskContext`, it has no way to know what the other
  items in the same compound command even are. Where should the "whole batch, one round trip"
  property actually live: inside `Session` (which has the full `MultiPermissionAskRequired.items`
  list up front, before its per-item loop starts), or somewhere in the TUI layer the plan names?
* Answer: Keep the actual classifier call triggered from `ReplApp._confirm_permission_ask`,
  exactly as the plan says, but add a new optional field to `klorb.session.PermissionAskContext`:
  `sibling_items: list[PermissionAskItem] | None`, set by `Session._resolve_multi_permission_ask`
  to the full `MultiPermissionAskRequired.items` list (including the item this context is itself
  about) on every context it constructs. `Session` still never calls the classifier, imports
  `klorb.permissions.risk_classifier`, or knows `tools.bash.riskClassifier.*` exists — it only
  threads data it already has. `ReplApp` doesn't classify anything itself either: it calls
  `klorb.permissions.risk_classifier.resolve_item_risk_assessment(ask_ctx, session=...,
  process_config=...)`, which owns the gating, batching (once per distinct `command_text`), and
  caching (each `ItemRiskAssessment` in `session.tool_state["BashRiskClassifier"]` keyed by
  `item_command_text`) — every subsequent item in the same batch (and a byte-identical retried
  item later in the session) hits that cache instead of re-classifying. Keeping this logic out of
  `klorb.tui.repl` means any other UI layer driving `Session` (a future non-TUI consumer, e.g. a
  VSCode plugin) can call the exact same function rather than re-implementing it against its own
  UI.
* Reasoning: The alternative — computing the whole-batch classification inside
  `Session._resolve_multi_permission_ask` itself, since that is where the full item list is
  naturally in hand before any per-item asking starts — was rejected because `Session` already
  reuses `self._provider` (the exact same `ApiProvider` a caller supplies, real or mocked) for
  its main conversation. Dozens of existing `test_session.py`/`test_tui_repl.py` multi-ask tests
  drive `Session`/`ReplApp` with a `MagicMock` provider whose `send_prompt.side_effect` is a
  short, exact list consumed in lockstep with the scripted turn sequence; an unconditional extra
  `send_prompt()` call from inside `Session`'s own tool-call resolution would desynchronize every
  one of those lists (`StopIteration`, or a mismatched reply fed into real turn-processing code)
  regardless of whether that particular test's items even carry `command_text`. Gating the call on
  `ask_ctx.command_text is not None` inside `ReplApp` instead costs nothing for the existing
  fixture tools (`AskPermissionTool`/`AskMultiPermissionTool`), which only ever raise path-based
  asks with no `command_text` at all, so none of those tests observe the new call. The one
  existing test that *does* construct a `command_text`-bearing `PermissionAskContext` directly
  (`test_confirm_permission_ask_truncates_a_long_single_line_command_to_fit_the_terminal`) still
  passes unmodified, because `classify_command_risk()`'s own broad failure handling degrades a
  bare, unconfigured `MagicMock().send_prompt(...)` reply to `None` exactly like a real API
  failure would, and the panel's existing fallback copy is unaffected either way.

  This also keeps `Session` — library code any future non-TUI consumer (a VSCode plugin) can
  drive without a `ReplApp` in the picture — free of a TUI-only ergonomics feature, matching this
  codebase's CLI/library split (see `CLAUDE.md`'s subprojects section) more closely than the
  plan's own prose implies without actually contradicting it: the plan's "why ReplApp" reasoning
  (no wasted round trip under `permission_framework` `"auto"`/`"deny"` or a headless run) holds
  either way, since `callbacks.on_permission_ask` is structurally never invoked in those modes
  regardless of which layer eventually calls the classifier.
