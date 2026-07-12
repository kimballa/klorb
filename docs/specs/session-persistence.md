# Session persistence

## Summary

A trusted workspace can pick a conversation back up where a previous interactive klorb
process left off. Quitting the TUI (`klorb.tui.repl.ReplApp`, via Ctrl+Q or the "Quit the
application" system command) offers to save the live `Session`'s `SessionConfig` and full
message history to `last-session.json`; opening klorb again in the same (trusted) workspace
auto-loads that file and replaces the freshly-constructed `Session` with one built from it,
re-rendering the history scroll to match. `klorb.workspace.last_session` owns reading and
writing the file; `klorb.tui.repl.ReplApp` owns the save prompt and the reconstruction.

## How it works

### Where the file lives

`last-session.json` lives in the same per-project directory as the prompt-input history file
— `$KLORB_DATA_DIR/projects/<token>-<basename>/last-session.json`, alongside `.../history`
(see `klorb.workspace.input_history.project_history_dir` and
docs/specs/projects-and-trust.md) — not inside the workspace itself. See
docs/adrs/store-last-session-under-klorb-data-dir-not-workspace.md for why. `<token>` is the
registered project's uuid, or a stable hash of the canonical workspace path for an
unregistered-but-trusted workspace, so both kinds of trusted workspace get one consistent
save slot.

### Saving (`ReplApp._quit_after_maybe_saving`)

`action_quit` (bound to Ctrl+Q, and reached via the built-in "Quit the application" system
command, and via `action_interrupt`'s Ctrl+C fallthrough when no shell command is running)
delegates to `_quit_after_maybe_saving`, a `@work()` worker — needed because it awaits a
`ConfirmScreen`'s dismissal, which Textual only permits from within an active worker's
context (the same reason `trust_workspace`/`_bootstrap_new_workspace` are workers — see
docs/specs/projects-and-trust.md).

* If this app has no `TrustManager` (`workspace_trust_management_enabled()` is `False`) or the
  current workspace isn't trusted, no prompt is shown — the app just exits. An unresolved or
  untrusted workspace has no business writing into its per-project data directory.
* Otherwise, asks "Save session state before quitting?"; on Yes,
  `klorb.workspace.last_session.write_last_session(workspace, session.config,
  session.messages)` writes the file (schema-enveloped per
  docs/specs/persisted-json-schema-versioning.md as `{"name": "klorb-session", "version":
  "1.0.0"}`), overwriting any previously-saved state for this workspace outright — there is
  only ever one "last" session per workspace, not a history of them.
* Either way, the app then exits (`self.exit()`).

### Restoring (`ReplApp._maybe_restore_last_session`)

Called from `_resolve_workspace_trust()` once the workspace is resolved, immediately after
attaching the input-history store, and only when the resolved workspace is trusted (the same
gate `_quit_after_maybe_saving` uses to decide whether to write). This runs before any
`initial_message` is submitted (`_run_startup_workspace_and_initial_message` awaits the whole
of `_resolve_workspace_trust()` first), so a `klorb -m "..."` invocation's message becomes the
next turn of the restored conversation rather than racing it.

`klorb.workspace.last_session.read_last_session(workspace)` returns `None` (a no-op) if no
file exists yet, or if it exists but its `schema.name` doesn't match (`read_versioned_json`
already discards and warns in that case). Otherwise:

1. The saved `SessionConfig` is copied with `workspace` overridden to the just-resolved
   `Workspace` (not the one recorded at save time) — trust and registration state are always
   taken fresh, never from the save file itself. No other saved field is reconciled against
   whatever config layers would produce for a brand-new session; the restored session's
   settings (model, thinking, permission rules, etc.) win outright, the same way `/clear`
   winning over a config file's declared defaults works elsewhere (see
   docs/specs/process-and-session-config.md).
2. The live `Session` is torn down (`Session.close()`) and replaced with a new one built from
   the restored config, reusing the outgoing session's `provider`/`model_registry` — the same
   pattern `ReplApp.clear_session()` uses.
3. `Session.load_messages(messages)` replaces the new session's (empty) history outright with
   the saved messages. Safe to call immediately after construction, before any `send_turn()`:
   a `role="system"`/`"tool_defs"` bookkeeping message already present is left as-is rather
   than duplicated (`_ensure_system_message`/`_ensure_tool_defs_message` each skip inserting a
   second one), and neither is ever replayed to the model anyway — the live system prompt and
   tool definitions are always resolved fresh and sent out-of-band on every turn, so whatever
   stale copy the restored history carries doesn't matter.
4. `_mount_restored_history(messages)` re-renders every restored message into the history
   scroll, in order, via the same `_mount_response_widget`/`_mount_thinking_widget`/
   `_mount_tool_call_widget` helpers a live turn uses, so a restored conversation looks the
   same as it would have live:
   * `role="user"` -> a `.prompt` `Static`, matching `_submit_prompt`'s echo.
   * `role="assistant"` -> `_mount_response_widget`, with a `*(interrupted)*` suffix appended
     when `processing_state == "aborted"` (mirroring `_handle_aborted_response`, whose marker
     lives only in the mounted widget, never in `Message.content` itself).
   * `role="thinking"` -> `_mount_thinking_widget`, `(interrupted)`-suffixed the same way.
   * `role="tool_use"` -> one `_mount_tool_call_widget` per `ToolCallRequest` in the message,
     rendered via `_render_restored_tool_call` (below).
   * `role="system"`/`"tool_defs"`/`"tool_response"` are never mounted on their own — matching
     how they're never rendered live either; a `tool_response` is folded into its matching
     `tool_use` entry instead.
   A final `.notice` ("Restored previous session (`N` messages).") is mounted after the
   replay, and the history is scrolled to the end.

### Reconstructing a tool call's display (`ReplApp._render_restored_tool_call`)

A live turn renders a tool call from a `ToolCallEvent` carrying the call's raw, unencoded
`(result, error)` pair (see `klorb.session.ToolCallEvent` and
docs/adrs/render-tool-calls-via-raw-callback-data.md) — but only the two persisted `Message`s
(`role="tool_use"`'s `ToolCallRequest.arguments`, and the matching `role="tool_response"`'s
`content`, joined by `tool_call_id`) survive a save/reload round trip. `content` is
`klorb.session._format_tool_response_content(result, error)`'s output: `"Error: {error}"` on
failure, otherwise `result` as-is if it was already a string, else its JSON encoding.
`_render_restored_tool_call` reverses that encoding best-effort:

* `call.arguments` is re-parsed as JSON; a decode failure is rendered exactly like a live
  invalid-arguments call (`default_invalid_tool_call_summary`/`_detail`), regenerating the
  same `"Invalid JSON in tool call arguments: ..."` message `Session._run_tool_calls` would
  have produced for the identical malformed input.
* Otherwise, `response.content` starting with `"Error: "` is treated as a failure (the
  remainder is `error`); anything else is treated as a success, parsed back from JSON if it
  looks like JSON, else kept as the raw string.
* Both branches hand off to `_render_tool_result` (the shared body `_render_tool_call` — the
  live path — also calls), which instantiates the named tool via `ToolRegistry` for its own
  `summary()`/`detail_view()`, or falls back to the shared default formatters if the tool
  isn't currently registered.

This is deliberately best-effort, not lossless: a successful string result that happens to
start with `"Error: "` is indistinguishable here from an actual failure, since both are folded
into the same `content` string when written and nothing else disambiguates them on the way
back. Every other shape (JSON-encodable results, `None`, actual errors) round-trips exactly.

## Configuration

No new `klorb-config.json` keys. Session persistence isn't itself configurable — it always
offers the save prompt for a trusted workspace with `TrustManager` support enabled, and always
auto-restores if a save file is present, exactly the same as the input-history store's own
always-on behavior for a trusted workspace.

## Out of scope

* No history of saved sessions — writing overwrites the workspace's one `last-session.json`
  outright, so only the most recently saved conversation is ever recoverable.
* Restoring replaces the live `Session`'s config outright with whatever was saved, rather than
  routing it through `klorb.process_config.load_process_config`'s config-layer precedence —
  `_load_last_session_overrides()` (`klorb.process_config`) is a placeholder reserved for that,
  never wired up (see docs/specs/process-and-session-config.md). This means a CLI flag like
  `--model` passed to a fresh invocation is superseded by a restored session's own `model`,
  unlike every config-file layer, where an explicit CLI flag always wins. This is a deliberate
  simplification: message history has no home in `ProcessConfig`'s layering regardless, and
  `last-session.json`'s saved config is a plain dump of `SessionConfig`'s own fields, not the
  on-disk `sessionDefaults` shape that layering step expects — reconciling the two would need a
  reverse mapping this feature doesn't otherwise require. A future change could route just the
  config portion through that pipeline if flag precedence turns out to matter in practice.
* The restored `SessionConfig` is not folded back into `ReplApp._process_config.session` (the
  template a future `/clear` copies from) — a `/clear` right after restoring reverts to
  whatever the process's own startup-time config layers produced, not the restored session's
  settings. A future change could dual-write this the way `_apply_workspace_config` does for
  `workspace`, if that turns out to matter in practice.
* No confirmation before restoring — unlike saving (an explicit Yes/No prompt), loading is
  unconditional whenever a save file exists for a trusted workspace, matching how the
  input-history store already auto-attaches with no prompt.
* A headless one-shot run never saves or restores anything — this is TUI-only, gated on
  `klorb.tui.repl.ReplApp`'s own `TrustManager`/workspace-trust machinery exactly like the
  input-history store (see docs/specs/projects-and-trust.md's "Out of scope" section, which
  notes the same limitation for that feature).
* A malformed or wrong-schema `last-session.json` is treated as "nothing to restore"
  (`read_last_session` returns `None`) rather than surfaced as a warning in the history scroll
  the way a `klorb-config.json` parse failure is (`ProcessConfig.config_warnings`) — a
  possible future improvement, not built here.
