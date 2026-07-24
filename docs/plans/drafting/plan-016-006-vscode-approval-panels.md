# Plan 016, increment 006: VS Code approval panels

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Replace the stub auto-reject from increment 002 with real interactive approvals in the
panel: permission asks (increment 005's requests) render as a panel docked directly above
the prompt input — the tall-narrow equivalent of the TUI's interaction panel — with the
allow/deny option grid, bash command previews with expansion, grant-pattern display,
free-text "other" redirection, the escalation variant, and the tool-call-limit prompt.
A permanent record of each decision lands in the history scroll.

## Deliverables

### 1. Host-side plumbing

* `KlorbAcpClient.requestPermission` now returns a `Promise` resolved by the webview:
  the host assigns a `requestId`, posts `permissionAsk {requestId, title, options,
  klorbMeta}` to the webview (options flattened to `{id, name, kind, scope}`;
  `klorbMeta` = the `_meta.klorb` payload verbatim: resourceDescription, commandText,
  itemCommandText, itemIndex/itemTotal, grantPatterns, riskLevel, escalation), and
  resolves when `permissionDecision {requestId, optionId, otherText?} | {requestId,
  cancelled: true}` comes back — encoding `otherText` into the ACP response's
  `_meta.klorb.otherText` per the server spec. Serial by design (the server asks one at
  a time); a second concurrent request queues behind the first in the host.
* Advertise `clientCapabilities._meta.klorb.raiseToolCallLimit = true` at initialize,
  and implement the `_klorb/raiseToolCallLimit` ext method as a
  `vscode.window.showWarningMessage(message, {modal: true}, "Continue")`-style modal
  (host-side, not webview — it's a rare safety interstitial, and a native modal reads
  with appropriate weight) returning `{approved}`.
* View-visibility guard: if the Klorb view is hidden when an ask arrives, call
  `vscode.window.showInformationMessage` with a "Show Klorb" action that focuses the
  view (`vscode.commands.executeCommand('klorb.sessionView.focus')`) so an approval
  can't sit invisible forever.

### 2. Webview `ApprovalPanel` (`src/webview/components/ApprovalPanel.tsx`)

Mounted in the interaction area (between history and input); while active, the prompt
input is disabled and visually muted (the TUI's interaction-mode treatment). Content,
top to bottom:

* Header: "Permission required" (or "Privilege escalation" when `klorbMeta.escalation`
  is set, styled with the error/warning accent), plus `itemIndex/itemTotal` ("2 of 3")
  when present.
* Body: `resourceDescription`; for bash asks, `itemCommandText` in a monospace block
  with a "show full command" disclosure revealing `commandText` (scrollable,
  height-capped — the TUI's `[more...]` expansion, inline instead of full-screen);
  `grantPatterns` rendered as a dim "grants: ..." line; `riskLevel`, when present, as a
  colored `<vscode-badge>`.
* Options: one `<vscode-button>` per option in a wrapping row — `allow_*` kinds primary,
  reject secondary; keyboard: buttons are tabbable, Escape cancels (→ `cancelled`,
  i.e. deny-once), matching the TUI's Escape semantics.
* "Other…" affordance: a disclosure revealing a one-line text field + send; submits
  `permissionDecision` with the deny-once option id and `otherText` — mirroring the
  TUI's free-text redirect.
* On decision: panel unmounts, input re-enables, and a compact record entry (new
  history-model kind `interaction`) is appended: the header line, the
  command/path/description shown, and `"Decision: <option name>"` — the TUI's
  `_record_interaction_history` equivalent.

### 3. Message protocol / model

Extend `webviewMessages.ts` (`permissionAsk`, `permissionDecision`) and
`historyModel.ts` (`interaction` entries; pending-ask state slot). The pending ask lives
in the model, not component state, so `vscode.setState` persistence keeps an
un-answered ask visible across a webview hide/show (`retainContextWhenHidden` already
helps; state persistence is the backstop) — an ask lost to a webview reload is
re-posted by the host (it still holds the unresolved promise; on webview
re-`resolveWebviewView` the host re-posts any pending `permissionAsk`).

## Tests

* `klorbAcpClient.test.ts` additions (mock agent): requestPermission → posted message →
  decision resolves the ACP response (selected / cancelled / otherText `_meta`);
  queued second ask; re-post of pending ask on webview reload hook; ext
  `_klorb/raiseToolCallLimit` returns approved/denied via faked modal.
* `ApprovalPanel.test.tsx` (jsdom): renders description/options; bash variant shows
  item command + full-command disclosure + grants line; option click posts the right
  decision; Escape posts cancelled; Other… posts otherText; escalation variant renders
  the distinct header.
* `historyModel.test.ts` additions: interaction record append; pending-ask state
  round-trips through serialization.

## Checkpoint criteria

* Both subprojects green; increments 001–005 behavior unchanged.
* Manual: in the real extension, run a bash-using prompt in an `ask` session; approve
  with "Allow for this session", see the command run and the decision recorded; verify
  the limit modal by setting `--max-tool-calls-per-turn 1` in a test config.
* `docs/specs/vscode-plugin.md` updated (panel anatomy, message shapes, re-post
  behavior, limit modal).
