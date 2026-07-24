# Plan 016, increment 005: Python permission asks over ACP

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Wire klorb's interactive approval flows into the protocol so a `permission_framework:
"ask"` session is fully usable from an ACP client: `on_permission_ask` becomes
`session/request_permission` (with the TUI's allow/deny × scope grid expressed as ACP
permission options), `on_escalate_privileges` rides the same request with klorb `_meta`,
and `on_tool_call_limit_reached` becomes the first client-directed extension request,
`_klorb/raiseToolCallLimit`. Server-side risk classification replicates what the TUI does
for bash asks. The generic client-side handling from 002 (auto-reject + log) already
answers these; the real panels land in 006.

## Deliverables

### 1. `on_permission_ask` → `session/request_permission`

In `TurnBridge` (blocking round-trip via `run_coroutine_threadsafe(...).result()`, after
draining queued updates — the 001 machinery):

* **Tool-call linkage.** ACP's request carries a `toolCall` (`ToolCallUpdate`) naming which
  call the permission belongs to. `PermissionAskContext` doesn't carry a call id, so the
  bridge tracks in-flight calls: push `call_id` on `on_tool_call_started`, pop on
  `on_tool_call`; a permission ask attaches to the most recent in-flight call (asks are
  raised from within `apply()`, so one is always live in practice). If the stack is
  empty (defensive), synthesize a `ToolCallUpdate` with a fresh id and the
  `resource_description` as title.
* **Options** (built in `update_mapping.py`, pure): option ids encode the decision axes
  the TUI's 2D grid offers (`PermissionDecision.action` × `scope`):
  * `allow:once` (kind `allow_once`), `deny:once` (kind `reject_once`) — always present.
  * `allow:session` (kind `allow_always`) — always present (in-memory session grant).
  * `allow:workspace`, `allow:homedir` (kind `allow_always`) — only when
    `resource.is_persistable` (a `StructuralResource` item gets only once/session/deny,
    matching the TUI).
  * Human names: "Allow once", "Allow for this session", "Always allow (workspace)",
    "Always allow (home config)", "Deny". Each option's `_meta.klorb.scope` carries the
    raw scope token so the client needn't parse ids.
* **klorb `_meta` on the request** (`_meta.klorb`): `resourceDescription`; for a bash ask
  (`bash_context` set): `commandText` (full command), `itemCommandText` (this item's
  statement — the client's prominent preview line, per
  docs/adrs/permission-ask-item-shows-its-own-command-text-not-the-full-compound.md),
  `itemIndex`/`itemTotal` for a multi-item (sibling) sequence, and `grantPatterns` /
  `riskLevel` from risk classification (below).
* **Decision mapping.** Response outcome `selected` → split the option id back into
  `PermissionDecision(action, scope)`; `cancelled` → `deny`/`once`. A `selected` outcome
  whose `_meta.klorb.otherText` is a non-empty string maps to
  `PermissionDecision(action="deny", scope="once", other_text=...)` — the free-text
  redirect the TUI's panel supports. `grant_patterns` is filled from the risk
  classification result when the chosen scope persists (see below), so the persisted rule
  matches what the client displayed — the exact invariant `PermissionDecision.
  grant_patterns` exists for.
* **Sibling batching / risk classification.** Port the TUI's behavior
  (`ReplApp._confirm_permission_ask`) into a server-side helper class
  (`klorb/src/klorb/server/risk_assessment.py`, encapsulating the per-turn cache): on the
  first ask of a `sibling_items` batch, call `classify_command_risk()` once for all items
  (same batching rationale as the TUI); cache per-item results keyed by item identity;
  each item's ask then carries its `suggested_pattern`-derived `grantPatterns` (falling
  back to `CommandResource.grant_preview()`) and `riskLevel`. Classifier failure degrades
  to grant-preview-only, never blocks the ask.
* **`permission_framework` interplay:** none of this fires for `"auto"`/`"deny"`
  sessions — that short-circuits inside `Session` already. Don't re-gate in the bridge.

### 2. `on_escalate_privileges` → `session/request_permission`

Same request machinery, two options (`allow:once` kind `allow_once` name "Approve for
this session", `deny:once` kind `reject_once` name "Deny"), with
`_meta.klorb.escalation = {scope, description}` from `EscalatePrivilegesContext` so the
client can render it as the distinct red-border flow it is. Maps back to
`EscalatePrivilegesDecision(approved=...)`. (Decision recorded in the spec: reusing
request_permission keeps stock-client compatibility — a stock client sees a
comprehensible approve/deny prompt.)

### 3. `_klorb/raiseToolCallLimit` extension request (agent → client)

* Server → client `ext_method` with params `{sessionId, message}` (the human-readable cap
  prompt `Session` builds), result `{approved: bool}`. Call it only when the client's
  `clientCapabilities._meta.klorb.raiseToolCallLimit` was advertised at initialize;
  otherwise return `False` immediately (cap stands, turn fails with
  `ToolCallLimitExceeded` — current headless behavior).
* Register the server's own advertisement too: `agentCapabilities._meta.klorb` gains
  nothing here (this is a client capability), but the spec's extension-method registry
  gains the method's full shape.

## Tests

* `test_update_mapping.py` additions (pure): option-set construction for persistable vs
  structural resources; option-id ↔ `PermissionDecision` round-trips incl. cancelled and
  otherText; escalation option/meta construction.
* `test_acp_server_permissions.py` (harness): a scripted turn whose tool raises a
  permission ask — harness client answers each option id in turn (parametrized) and the
  test asserts the tool retried/failed accordingly (observable through the turn's
  tool_call_update stream and the session's recorded grant state for
  session-scope answers); multi-item bash ask arrives as N serial requests with
  correct `itemIndex`/`itemTotal` and one classifier invocation (classifier faked at the
  `classify_command_risk` seam — asserting batch-once, not re-testing the classifier);
  escalation ask maps approved/denied both ways; limit-reached with capability
  advertised → ext request, approved doubles the cap (assert the turn continues);
  without the advertisement → no ext call, turn errors.
* No re-testing of `Session`'s own ask/retry internals beyond what the assertions above
  need — the session suite owns that.

## Checkpoint criteria

* `make -C klorb lint typecheck test` green.
* Manual: `permission_framework` defaults to ask; from the hand-driven client recipe,
  answer a `session/request_permission` for a `Bash` call with `allow:session` and watch
  the command run. VS Code panel (002-004 state) still functions — its stub client
  auto-rejects asks, i.e. tools get denied until 006 lands, which is safe.
* Spec updated: full request/response shapes, `_meta.klorb` fields, extension-method
  registry entry, risk-assessment flow.
