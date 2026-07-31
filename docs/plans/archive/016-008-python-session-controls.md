# Plan 016, increment 008: Python session controls over ACP

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Everything the TUI exposes for steering *how the session operates* becomes reachable over
the protocol: permission framework as ACP session modes; model choice and thinking
enabled/effort as ACP session config options (with a defined fallback); session naming and
token-usage reporting as session updates; plus small extension requests for session stats,
workspace trust, and skills reload. Pure-TUI concerns (themes, keybinds) get nothing.

## Deliverables

### 1. Permission framework ⇄ session modes

* `new_session`'s response advertises the mode state: three modes
  (`ask` "Ask before acting", `auto` "Auto-approve", `deny` "Deny tool asks" — ids match
  `PermissionFramework` literals) with `currentModeId` from the session's config.
* `set_session_mode(session_id, mode_id)` → `Session.set_permission_framework(mode_id)`
  (the method that also queues the model-facing interjection — do not assign the config
  field directly; see docs/specs/permissions.md).
* When the framework changes for any reason, emit `session/update` →
  `current_mode_update` — so a client-initiated change is confirmed and any future
  server-side change is broadcast.

### 2. Model + thinking ⇄ session config options

Using the SDK's session config option surface (select + boolean options on the session,
`set_config_option` to change them):

* `model` (select): options from `ModelRegistry` (id = registry model name, display name
  = the same string the TUI palette shows), current value from
  `Session.active_model_name()`. Setting it assigns `session.config.model` (what the TUI's
  `select_model()` does).
* `thinking.enabled` (boolean) → `config.thinking_enabled`.
* `thinking.effort` (select: low/medium/high) → `config.thinking_effort`.
* After any change, re-emit the config-option state so the client's UI converges.
* **Fallback (decide at implementation time, record in the spec):** if the pinned SDK's
  config-option support is missing pieces in practice, expose the same data instead via
  `_klorb/getSessionConfig` / `_klorb/setSessionConfig` ext requests with an equivalent
  JSON shape, and advertise `agentCapabilities._meta.klorb.sessionConfig = true`. The
  client increment (009) is written against a thin host-side interface so either wire
  shape plugs in.

### 3. Session identity + usage updates

* `on_session_name_changed` → the SDK's session-info update (title) when the pinned
  version supports it, else `_klorb/sessionNamed {sessionId, title | null}` notification.
  Also return the current name (when already set) in `new_session`-adjacent metadata so a
  client attaching later still learns it.
* Token tally: after each turn completes (success, error, or abort), emit the usage
  update the SDK supports — or `_klorb/usage {sessionId, usedTokens, maxTokens | null}`
  notification — computed from `Session.total_tokens_used()` /
  `Session.max_context_window()`, the same numbers the TUI status bar shows. Per-chunk
  emission is deliberately avoided (chatty, and the TUI itself only refreshes per turn).
* `_klorb/sessionStats` ext request → the `SessionStatistics` payload the TUI's "Show
  session stats" renders, as JSON.

### 4. Workspace trust

* `new_session` result carries `_meta.klorb.workspace = {path, trusted}`.
* `_klorb/trustWorkspace {sessionId}` ext request: applies the same trust the TUI's
  "Trust workspace" flow applies (persist via `TrustManager`, then re-apply
  workspace-dependent session state the way `_apply_workspace_config` does). Requires the
  server to be constructed with trust management enabled (it resolves the same
  `TrustManager` the TUI uses); when unavailable, the request errors cleanly. Trusting
  mid-session triggers the same context-file seeding rules the TUI path produces — assert
  behavior through the session's public surface, don't fork new logic.
* Note: sessions in untrusted workspaces run exactly as an untrusted TUI session does
  (no context-file interjection, read boundary enforced) — no new policy here.

### 5. Housekeeping ext requests

* `_klorb/reloadSkills {sessionId}` → the skill-catalog rebuild the TUI command runs;
  returns `{skillCount}`.
* Advertise every implemented flag under `agentCapabilities._meta.klorb`
  (`sessionStats`, `trustWorkspace`, `reloadSkills`, plus `sessionConfig` if the fallback
  path was taken).

## Tests

Harness-based (`test_acp_server_session_controls.py`) plus pure mapping tests:

* set_session_mode round-trip: mode change lands in config via
  `set_permission_framework` (assert the interjection queued — the observable that
  proves the right method was used), `current_mode_update` emitted.
* Config options: listing reflects registry models + current values; setting model /
  thinking fields mutates config; invalid option id/value → JSON-RPC error.
* Naming: fake `generate_session_name` (the conftest seam) to return a name; assert the
  title update fires once with the derived title, and `None` on failure.
* Usage update after a scripted turn matches `total_tokens_used()`.
* `_klorb/sessionStats` returns the statistics JSON; `_klorb/reloadSkills` hits the
  catalog seam; `_klorb/trustWorkspace` flips trust via a tmp-dir `TrustManager` and a
  subsequent turn seeds context files (assert via the prompt the scripted provider
  receives — the same style existing workspace-context tests use).

## Checkpoint criteria

* `make -C klorb lint typecheck test` green; VS Code client unaffected (it ignores the
  new surface until 009).
* Manual: hand-driven `set_session_mode` to `auto`, watch a tool run without asks;
  set model via config option and confirm the next turn's request uses it.
* Spec updated: modes table, config-option ids, every ext method's shape, trust flow.
