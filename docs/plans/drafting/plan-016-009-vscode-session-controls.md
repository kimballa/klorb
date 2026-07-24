# Plan 016, increment 009: VS Code session controls

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Surface increment 008's control plane in the client: a status row in the panel (model +
thinking, permission-mode badge, token tally, session title), native pickers for model and
thinking, a new-session command, session stats, and workspace-trust bridging. After this
increment the plugin has functional parity with every session-affecting TUI command.

## Deliverables

### 1. Host-side control interface (`src/sessionControls.ts`)

A thin class over `AcpConnection` exposing typed methods — `listModes/setMode`,
`getConfigOptions/setConfigOption` (works against either the SDK config-option surface or
the `_klorb/…SessionConfig` fallback, chosen by the capabilities learned at initialize),
`sessionStats()`, `trustWorkspace()`, `reloadSkills()` — plus events for
`current_mode_update`, config-option state, session title, and usage updates, forwarded
to the webview as new host messages (`statusUpdate {model, thinkingEnabled,
thinkingEffort, permissionMode, usedTokens, maxTokens, sessionTitle, workspaceTrusted}`
— one coalesced shape; the host owns merging the various ACP notifications into it).

### 2. Webview status row (`src/webview/components/StatusRow.tsx`)

Docked under the prompt input, one line, ellipsizing gracefully at narrow widths:

* Model chip: `model (Effort)` when thinking is on, model alone when off (the TUI header's
  format). Click → posts `pickModel` intent.
* Permission badge: `[ask]`/`[auto]`/`[deny]` with the TUI's bracketed look; click cycles
  ask → auto → deny → ask (posts `cyclePermissionMode`), with a brief highlight flash on
  change (CSS transition; keep the two-step animation spirit without the TUI's exact
  timings).
* Token tally: `used / limit` with the TUI's SI formatting — port `format_token_count`'s
  rules into a pure `formatTokenCount.ts` (raw < 1000; k/M/B at 2 significant figures)
  and unit-test it; omit ` / limit` when max is null.
* Session title: "New session…" placeholder until a title/name update arrives (TUI status
  line semantics).

### 3. Pickers and commands (extension host, native UI)

Native `vscode.window.showQuickPick` — not webview panels — for infrequent control
changes (they're VS Code-idiomatic and free):

* `klorb.selectModel`: QuickPick of the model options (current one marked); on pick,
  `setConfigOption('model', …)`.
* `klorb.setThinking`: QuickPick — "Enable thinking", "Disable thinking", then effort
  QuickPick (low/medium/high, current marked) when enabling or via
  `klorb.setThinkingEffort`.
* `klorb.cyclePermissionMode` (also reachable from the badge): `setMode(next)`.
* `klorb.newSession`: replaces `klorb.restartSession`'s meaning cleanly — calls
  `newSession` (server closes the old one per the single-session policy), posts
  `sessionReset` to the webview. Keep `klorb.restartServer` as-is.
* `klorb.showSessionStats`: fetches `_klorb/sessionStats` and renders the same lines the
  TUI notice shows, in a `vscode.window.showInformationMessage`-backed detail or an
  output-channel dump (choose one, spec it).
* `klorb.reloadSkills`: calls the ext method, toast with the count.
* All contributed in `package.json` with "Klorb: …" titles.

### 4. Workspace trust bridging

* On connect, if VS Code's own workspace trust (`vscode.workspace.isTrusted`) is granted
  but the klorb workspace is untrusted, show a one-time prompt ("Trust this workspace in
  Klorb? The agent gains read access beyond the workspace root and context files are
  loaded.") with Trust / Not now → `trustWorkspace()` on accept. If VS Code itself is in
  Restricted Mode, don't offer (listen to `onDidGrantWorkspaceTrust` to offer later).
  Status row shows an `(Untrusted)` suffix on the title until trusted (TUI header
  parity).

## Tests

* `sessionControls.test.ts` (mock agent): both wire shapes (config options vs `_klorb`
  fallback) behind the same interface; statusUpdate coalescing from interleaved
  notifications; trust/stats/reload round-trips.
* `formatTokenCount.test.ts`: port the TUI's formatting cases (1400 → "1.4k", 423000 →
  "420k", <1000 raw, null max).
* `StatusRow.test.tsx`: render states (thinking on/off, untrusted suffix, no-limit
  tally); clicks post the right intents.
* Command tests with the faked `vscode` surface: QuickPick flows call the control
  interface with the picked values; trust prompt logic honors the isTrusted /
  restricted-mode matrix.

## Checkpoint criteria

* Both subprojects green.
* Manual: switch model from the status chip and see the next turn use it; cycle the
  badge to `auto` and watch tools run unprompted; new-session clears the panel; stats
  and skills-reload commands work; untrusted-workspace flow end to end.
* `docs/specs/vscode-plugin.md` updated (status row anatomy, command table, trust
  bridging rules).
