---
name: vscode-tech-planning
description: Use when drafting or reviewing a system/technical plan (docs/plans/) that includes a phase for vscode-plugin work, especially one framed as "port/translate the TUI integration to the vscode plugin." Checks whether the plan accounts for the ACP wire-protocol and host<->webview messaging work the TUI bypasses, not just webview UI. Trigger on phrases like "vscode plugin UI dev", "bring X to the vscode plugin", or any plan phase that follows a TUI-integration phase.
---

# Planning vscode-plugin work: the TUI doesn't cross the wire, the plugin does

The TUI calls `Session`/library code in-process, in the same Python process, in the same
function call. The vscode plugin never does: it's a separate `klorb server` child process
talking ACP (JSON-RPC over stdio) to the VS Code extension host, which then talks a second,
separate typed message protocol to the sandboxed webview. A plan phase that reads "do the same
thing as the TUI phase, but in the vscode plugin" is silently assuming those two wire crossings
already carry the data — that assumption is exactly what got missed in a past system plan, whose
"vscode plugin UI dev" phase was scoped as a straightforward translation of a TUI-integration
phase and skipped both crossings entirely.

## The three layers a vscode-facing feature must cross

Any `Session`-level behavior the TUI reads directly (a new turn event, a new piece of state, a
new user-facing control) needs a plan step for each layer it doesn't already cross:

1. **Python ACP surface** (`klorb/src/klorb/acp/` or wherever `KlorbAcpAgent` lives) — does the
   new behavior already have an ACP expression? If not, it needs one: a new `session/update`
   variant, a new `_klorb/*` extension method, or a new field on an existing response, plus a
   matching capability flag in `agentCapabilities._meta.klorb` (see
   `docs/specs/klorb-server.md`'s "Extension methods" section for the existing catalog and the
   capability-flag convention). This is server-side work independent of the vscode plugin —
   it's what makes the feature reachable over stdio at all.
2. **Extension host** (`vscode-plugin/src/host/features/acp/`) — `AcpConnection`/
   `KlorbAcpClient` need to handle the new ACP message, and per
   `docs/adrs/00156-vscode-webview-stays-acp-ignorant-behind-typed-messages.md`,
   `KlorbSessionViewProvider` needs to re-express it as a new variant on the shared
   `HostMessage`/`WebviewMessage` union in `vscode-plugin/src/shared/webviewMessages.ts`. The
   webview never sees ACP directly — this translation step is mandatory, not optional glue.
3. **Webview** (`vscode-plugin/src/webview/`) — a reducer-style handler (the `historyModel.ts`
   pattern) needs to fold the new `HostMessage` into React state, and only then does rendering
   become a pure UI question (control choice, icon, placement — see the `vscode-plugin-ui`
   skill for that part specifically).

A plan phase that only describes step 3 — components, layout, icons — has skipped the two steps
that make the data exist in the webview to render in the first place.

## What to check before accepting a "vscode plugin UI dev" phase

- [ ] Does the TUI-side behavior this phase is "porting" already have an ACP expression (check
      `docs/specs/klorb-server.md`'s extension-method catalog), or is the TUI reading it straight
      off `Session` with nothing on the wire?
- [ ] If there's no existing ACP expression: is there a phase (or phase split) for the
      Python-side protocol addition — new `session/update` payload, new `_klorb/*` method, new
      capability flag — separate from and prior to the vscode UI phase? Look at how plan 016 did
      this (`docs/plans/archive/016-*.md`): each capability got a `python-*` phase before its
      matching `vscode-*` phase (e.g. `016-005-python-permission-asks.md` before
      `016-006-vscode-approval-panels.md`), never a single combined phase.
- [ ] Is there an explicit step for extending `webviewMessages.ts`'s `HostMessage`/
      `WebviewMessage` union and `KlorbSessionViewProvider`'s translation logic, or does the plan
      jump straight from "ACP message arrives" to "component renders it"?
- [ ] Does the phase name or description imply the vscode work is "just UI"/"just like the TUI"?
      That phrasing is the tell — rewrite the phase (or split it) so the protocol and
      host-translation work are named as their own steps, even if scoped small.
- [ ] For a feature that's genuinely UI-only (rendering data the plugin already receives, no new
      ACP surface needed) — confirm that by naming the existing `session/update`/`_klorb/*`
      method the data already arrives through, not just by assuming it.
