# Avoid toast notifications; report status changes as history notices instead

* Date: 2026-07-06
* Question: klorb's REPL used Textual's built-in `App.notify()` toast (an ephemeral overlay
  stacked in the lower-right corner) to confirm settings changes and one-off command results
  — "Model set to {name}.", thinking enabled/disabled, thinking effort set, session cleared,
  and `>Init local klorb config`'s outcome. The REPL also has a scrollback history (the
  `#history` `VerticalScroll`) with an established `.notice` CSS class already used for
  similar confirmations (`select_theme`, the missing-user-config nudge, workspace-trust
  messages — see [[terminal-repl]]/[[klorb-init]]). Should status confirmations keep using
  the toast, the history notice, or both, going forward?
* Answer: History notice only. All six toast call sites (`ReplApp.select_model`,
  `set_thinking_enabled`, `set_thinking_effort`, `clear_session`, and
  `InitCommandProvider._run_init`'s success/error paths) were converted to append a `Static`
  with CSS class `.notice` (or `.error` for the init failure case) to `#history`, matching
  the pattern `select_theme` already used. `InitCommandProvider` reaches the app through a
  new `ReplApp.show_notice(message, *, error=False)` method (replacing the old
  `SupportsNotify`/`notify` structural `Protocol` with `SupportsShowNotice`/`show_notice`),
  since a `Provider` living outside `repl.py` shouldn't reach into `HISTORY_ID`/
  `VerticalScroll` directly. No new toast call sites should be added; any future one-off
  status message belongs in the history scroll via this same pattern instead.
* Reasoning: klorb's REPL has a scrollback the user can review at any time — unlike a toast,
  which auto-dismisses and is gone if the user was looking elsewhere (e.g. mid-typing) when
  it appeared. A notice that lands in `#history` is scrollable, persists for the life of the
  session, and sits in the same chronological log as the prompts/responses/tool calls it's
  contextualizing (e.g. "Model set to X" now visibly precedes the next turn that used model
  X). It also collapses two parallel "tell the user something happened" mechanisms into one,
  removing the need for a second CSS-class vocabulary (toast severities) alongside the
  history's own (`.notice`/`.error`/`.prompt`/etc.). The one-time cost is that a notice
  doesn't auto-dismiss, but `.notice`'s muted styling (`color: $text-muted`) already keeps it
  visually secondary to actual conversation content.
