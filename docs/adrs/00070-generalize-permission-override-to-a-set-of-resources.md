# Generalize the once-only bypass to a `PermissionOverride` set of paths/commands/reasons

* Date: 2026-07-07 23:54
* Question: [The original once-grant ADR](once-grants-bypass-via-tool-context-override-not-table.md)
  gave `ToolSetupContext` a single `permission_override: Path | None` field so a retried tool call
  could bypass exactly one directory-access check without persisting anything, and explicitly
  flagged that a hypothetical future tool touching multiple resources in one call would need "a
  different one-shot-bypass design entirely." Serial multi-item asks
  (see [that ADR](ask-independent-items-serially-not-just-the-strictest.md)) made that
  hypothetical real: a compound `BashTool` call can raise several independent `"ask"` items —
  some `path`s, some bare `command` argvs, some structural reasons with neither — and the user
  can answer several of them `"once"` in the same round of prompts. A single `Path | None` field
  can't represent "these three different resources are all bypassed for this one retry."
* Answer: `ToolSetupContext.permission_override` is now `klorb.permissions.table.
  PermissionOverride | None` — a small value type holding three sets: `paths: frozenset[Path]`,
  `commands: frozenset[tuple[str, ...]]`, and `reasons: frozenset[str]`, one per
  `PermissionAskItem` shape. `evaluate_write`/`resolve_and_evaluate_read`
  (`klorb.permissions.workspace`) check `path in override.paths`, exactly as they checked equality
  before. `BashTool._classify` gained the analogous checks for the other two: a simple command's
  exact argv tuple in `override.commands` skips `CommandPermissionsTable` entirely, and a forced-
  ask reason string in `override.reasons` is dropped rather than turned into another
  `PermissionAskItem`. `Session._retry_after_multi_permission_decisions` collects every
  `scope="once"` item's identifying value (`item.path`/`tuple(item.command)`/
  `item.resource_description`, whichever the item carries) into the three sets and builds one
  `PermissionOverride` for the whole retried call — still exactly one retry per compound call, not
  one per item, matching the serial-ask ADR's own reasoning against duplicate work.
* Reasoning: The alternative — three separate `ToolSetupContext` fields, one per resource shape —
  works but scatters the "what does this one-shot bypass mean" concept across three independently-
  named, independently-`None`-checked fields that always travel together (every caller building
  one either has none of them or fills in whichever subset applies to that call's items). A single
  aggregate type keeps `ToolSetupContext` at one field, keeps the "no override at all" case a
  single `None` check, and gives the three sets a natural home to document their shared contract
  (mirrors `PermissionAskItem`'s own "paths/commands/neither" shape) together rather than
  separately. Extending the *type* of the existing field, rather than introducing a second
  mechanism alongside it, also means every existing single-item call site
  (`Session._retry_after_permission_decision`) only had to change how it constructs the value
  (`PermissionOverride(paths=frozenset({path}))` instead of a bare `path`), not add a parallel
  code path.

  A structural item (no `path`, no `command`) still can't be granted at any scope other than
  `"once"` — there's no rule to write for "this unrecognized construct is now always fine" — but
  before this change it also couldn't reliably honor even `"once"`: retrying re-parses the exact
  same command, which deterministically reproduces the same forced-ask reason with no identifier
  to bypass by. `override.reasons` closes that gap by using the reason text itself as the bypass
  key, which is sound only because `klorb.permissions.shell_parse.parse_command` is pure and
  deterministic for a given command string — the same command always produces the same reason
  text on every parse, so matching on it for one retry is safe.
