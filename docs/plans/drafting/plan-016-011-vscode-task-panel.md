# Plan 016, increment 011: VS Code task panel

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Render increment 010's plan updates as the task panel: a collapsible strip docked at the
top of the sidebar (the tall-narrow adaptation of the TUI's Ctrl+T right-hand sidebar).
Collapsed by default to a one-line summary; expanded, it lists every issue with the
current task starred and closed issues struck through.

## Deliverables

* **Protocol/model:** host flattens ACP `plan` updates (preferring per-entry
  `_meta.klorb` when the server advertised `taskMeta`) to a `taskListUpdate {summary:
  {openCount, closedCount, blockedCount, currentTaskId}, tasks: [{issueId, text,
  priority, status, blocked, isCurrentTask, closed}]}` host message; new `taskList` slot
  in the webview model (replaced wholesale per update — snapshots, not deltas),
  persisted via `vscode.setState` like the rest.
* **`TaskPanel` component** (`src/webview/components/TaskPanel.tsx`):
  * Collapsed header row (always visible once any plan update has arrived):
    a `checklist` codicon + "Tasks: 3 open · 1 blocked · #12 in progress" built from the
    summary; click / Enter toggles expansion (`<vscode-collapsible>` if it behaves under
    the bundle, else a hand-rolled disclosure — implementer's choice, spec the outcome).
  * Expanded: scrollable list, height-capped (~40% of panel height via CSS `max-height`)
    so history stays usable; each row `#id title` — current task gets a leading ★,
    closed rows dim + strikethrough, blocked rows a dim "(blocked)" suffix — the TUI
    sidebar's exact visual grammar, restyled.
  * Empty states: no plan update yet but server advertised `taskMeta` → nothing rendered
    at all (no dead chrome); a plan update with zero entries → header shows
    "Tasks: none".
* **Visibility toggle:** `klorb.toggleTaskPanel` contributed command (parity with
  Ctrl+T) posts a toggle message; also a small pin/unpin in the header. State persists
  with the rest of the webview state.
* No client-side chainlink access whatsoever — the server snapshot is the only source
  (unlike the TUI, which shells out itself; note this asymmetry in the spec).

## Tests

* `klorbAcpClient.test.ts` additions: `plan` update (with and without `taskMeta`
  detail) → `taskListUpdate` flattening, including the degraded stock-ACP path (no
  meta: statuses only, ids absent).
* Model tests: wholesale replacement semantics; persistence round-trip.
* `TaskPanel.test.tsx`: summary line construction, expansion toggle, star/strike/blocked
  row rendering, empty states.

## Checkpoint criteria

* Both subprojects green.
* Manual: agent creates/closes todos over a conversation; the panel's summary and rows
  track live, current task starred; collapse state survives hiding the sidebar.
* `docs/specs/vscode-plugin.md` updated.
