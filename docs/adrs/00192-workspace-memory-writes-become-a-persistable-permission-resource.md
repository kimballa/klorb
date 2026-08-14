# Workspace memory create/edit/delete asks become a persistable MemoryResource

* Date: 2026-08-14 00:00
* Question: Every memory tool enforced its verdict via a path-less
  `klorb.permissions.table.raise_if_not_allowed(verdict, resource_description=...)` call, which
  builds a `klorb.permissions.resource.StructuralResource` — a resource kind whose
  `is_persistable` is hardcoded `False`. `Session._run_tool_calls` unconditionally fails any
  non-persistable ask closed *before* ever consulting `permission_framework` or calling
  `callbacks.on_permission_ask` — so a memory `"ask"` verdict could never show an interactive
  panel, honor a session-scoped grant, or be auto-approved under `permission_framework="auto"`,
  in either the TUI or the ACP/VSCode frontend. A `CreateMemory` call against an `"ask"`-gated
  operation surfaced only the raw exception string as the tool response, with the agent unable to
  proceed. Should memory writes become a real persistable resource, and at what granularity
  (per-operation, per-namespace, per-filename)?
* Answer: Add `MemoryResource(access, filename)` to `klorb.permissions.resource` —
  `access` is `"write"` (covering both `CreateMemory` and `EditMemory` under one config knob and
  one persistent grant) or `"delete"` (`ForgetMemory`) — as a new persistable `PermissionResource`
  kind, so a `workspace`-namespace write/delete ask flows through the ordinary ask-panel/
  session-grant/auto-mode machinery, exactly like a skill activation. There is no `namespace`
  field: every `MemoryResource` instance is implicitly about the `workspace` namespace, because
  `global` memories and every `read` operation (in both namespaces) are hardcoded-allow — they
  never call `raise_if_not_allowed` at all, not even with an `"allow"` verdict, so they can never
  raise this resource. The once-scope override key is `(access, filename)` — precise to the exact
  resource that was asked about, matching `PathResource`'s own once-override precision — while the
  persistent (session/workspace/homedir) grant is coarser, keyed only on `access`, since the
  underlying config model (`SessionConfig.memory_write_permission`/`memory_delete_permission`) is
  a flat scalar `Verdict` per operation, not a per-filename rule table.

  The two remaining knobs move from `ProcessConfig` to `SessionConfig` (their on-disk keys moving
  from top-level `klorb-config.json` keys to `sessionDefaults.*` keys), matching every other
  ask-able rule table's placement — required for a `"session"`-scope grant to actually be
  per-session rather than leaking to every session in the process, which is what would happen if
  they stayed on the process-wide `ProcessConfig`. `memory_write_permission`'s default becomes
  `"ask"` (previously two separate knobs: edit defaulted `"allow"`, create defaulted `"ask"`).
  `memory_read_permission` is deleted outright, not merely left unused: with read unconditionally
  allowed in both namespaces, there is no remaining verdict for it to hold.
* Reasoning: The generic ask protocol itself needed no changes — `klorb.tui.panels.
  permission_ask_panel.PermissionAskPanel` and the ACP mapping in `klorb.server.update_mapping`
  both render entirely off `PermissionResource`'s polymorphic methods (`header_kind()`,
  `preview_text()`, `grant_preview()`, `is_persistable`) with no resource-kind-specific branching,
  so giving memory writes a real resource was sufficient to fix both the TUI and the ACP/VSCode
  frontend at once; this was never a VSCode-specific bug, just less visible in the TUI because it
  rendered the raw error text inline instead of a blank panel.

  `global` and every `read` stay hardcoded-allow rather than becoming configurable-to-`"ask"`,
  because they carry no risk this permission system exists to gate: a `global` memory lives
  entirely under the user's own home directory, with no workspace-supplied content for a hostile
  repository to plant or redirect through, and a read can't itself persist anything at all. Only a
  `workspace`-namespace write or delete touches content that could plausibly have been shaped by
  the workspace itself (e.g. a poisoned `.klorb/memories/` file checked into a hostile repo, or a
  secret the model is tricked into writing there for later exfiltration via a commit) — that is
  the actual thing worth asking about, and the only thing this change adds an ask path for.
