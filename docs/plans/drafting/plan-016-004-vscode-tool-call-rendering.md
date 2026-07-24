# Plan 016, increment 004: VS Code tool-call rendering and editor integration

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Render increment 003's tool-call updates in the panel: a chip per tool call showing a
running state while in progress and a one-line summary when done, expandable to detail —
and, where a call names files, integration with the editor itself: click to open the file
at the right line, or open a real diff view for edits. This is the TUI's tool-call
display (`<Tool use>` blocks, running animation, Ctrl+O detail, click-to-expand previews)
translated to VS Code idioms.

## Deliverables

### 1. Message protocol + history model

* Extend `src/shared/webviewMessages.ts`:
  * Host → webview: `toolCallStarted {callId, title, kind, locations}`,
    `toolCallUpdated {callId, status, title?, contentText?, diff?, locations}` where
    `diff` is `{path, hunks}` when the server sent `_meta.klorb.diffHunks` (preferred) or
    `{path, oldText, newText}` from a plain ACP diff block. The extension host
    (`KlorbAcpClient.sessionUpdate`) performs this ACP→message flattening.
  * Webview → host: `openLocation {path, line?}`, `openDiff {callId, path}`.
* `historyModel.ts`: new entry kind `toolCall` keyed by `callId` — `toolCallUpdated`
  mutates the matching entry in place (status/title/content), preserving history order;
  an update for an unknown `callId` appends (mirrors the TUI's fallback for calls that
  never got a started event, e.g. malformed-arguments calls).

### 2. `ToolCallChip` component (`src/webview/components/ToolCallChip.tsx`)

* Collapsed row: kind icon (`<vscode-icon>` codicons: `book` read, `edit` edit, `search`
  search, `terminal` execute, `globe` fetch, `checklist` think, `tools` other), title
  text, and state: an in-progress call shows a `<vscode-progress-ring>` (small) — VS
  Code-native busy affordance in place of the TUI's crawling-text animation; a failed
  call shows an error-colored `error` codicon.
* Expand/collapse per chip (chevron): detail area shows `contentText`, or for a diff a
  colored hunk view (green add / red del, line-number gutter, reusing the hunk JSON
  verbatim — a small pure `renderDiffLines(hunks)` helper returns row models the
  component maps; unit-test the helper, not the pixels). A global "expand all tool
  calls" toggle button in the history header mirrors the TUI's Ctrl+O (new chips follow
  the current global mode, matching the TUI's behavior).
* Editor links: when `locations` is non-empty, the title becomes a link posting
  `openLocation`; diff chips get an "Open diff" action posting `openDiff`.

### 3. Extension-host editor integration (`src/editorIntegration.ts`)

* `openLocation`: `vscode.window.showTextDocument(Uri.file(path))`, with
  `selection` at `line` when given. Nonexistent path → `vscode.window.showWarningMessage`
  (the TUI's "Could not reopen" equivalent).
* `openDiff`: reconstruct before/after contents from the stored diff payload for that
  `callId` (host keeps a bounded per-session map of diff payloads) and show
  `vscode.diff` between two read-only virtual documents (a
  `TextDocumentContentProvider` registered under a `klorb-diff:` scheme), titled
  `"<basename> (Klorb edit)"`. Note in the spec: hunk-reassembled contents are an
  elided view, not the whole file — same caveat the server spec records.

## Tests

* `historyModel.test.ts` additions: started→updated in-place transition, unknown-callId
  append, interleaving with chunks preserves order, global-expand state application.
* `renderDiffLines.test.ts`: add/del/context rows, gutter numbering, multi-hunk
  separators.
* `App.test.tsx` additions: a scripted started+updated pair renders a chip that goes
  busy→completed; clicking the title posts `openLocation` with the payload.
* `klorbAcpClient.test.ts` (mock agent from 002): ACP `tool_call`/`tool_call_update`
  notifications (incl. a diff block with `_meta.klorb.diffHunks`) flatten to the expected
  host messages.
* `editorIntegration.test.ts`: with a faked `vscode` API surface (module-injected, per
  the existing pattern of keeping `vscode` imports at the edges), `openLocation` and
  `openDiff` invoke the right VS Code calls; missing diff payload → warning path.

## Checkpoint criteria

* Both subprojects' `make` targets green.
* Manual: run a prompt that edits a file; watch the chip go busy→done, expand to see the
  colored diff, open the real diff editor, and click a read call through to its file/line.
* Spec (`docs/specs/vscode-plugin.md`) updated: chip states, codicon mapping, message
  shapes, virtual-document diff scheme.
