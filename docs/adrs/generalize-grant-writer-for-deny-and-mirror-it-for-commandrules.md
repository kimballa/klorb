# Generalize the grant writer for Deny scope, and mirror it as `CommandRules`' own grant writer

* Date: 2026-07-07 23:54
* Question: `klorb.permissions.grant.apply_permission_grant()` only ever wrote `Allow` entries —
  a persistent-scope `"deny"` answer to a `PermissionAskContext` had no writer to call at all.
  Once the 2D allow/deny × scope grid (see docs/specs/permissions.md) let a user pick a
  persistent `Deny` the same way they'd pick a persistent `Allow`, something had to turn that into
  a `DirRules.deny` entry the same way `apply_permission_grant()` already turned an `Allow`
  decision into a `DirRules.allow` entry. Separately, `BashTool`'s command-pattern ask items
  (`CommandRules`) had no persistent-grant writer at all, of either direction — only directory
  items could be promoted to a rule; a command item's `"session"`/`"workspace"`/`"homedir"`
  decision had nowhere to go.
* Answer: `apply_permission_grant()` gained a `GrantAction = Literal["allow", "deny"]` first
  parameter. Its internal `_apply_decision_to_table()` (renamed from `_promote_table`, which only
  ever promoted toward `allow`) now writes the granted paths into `rules.allow` or `rules.deny`
  depending on `action`, always removing them from `rules.ask` either way (a granted path, allowed
  or denied, is no longer merely "unsure"). For `action="deny"` with `is_write=True`, only
  `writeDirs` is touched — denying a write doesn't imply denying a read that was never in
  question, unlike the `allow` direction, which always promotes the matching `readDirs` entry too
  (see [the read/write-union ADR](union-matched-ask-rules-across-read-and-write-tables.md)).
  `klorb.permissions.command_grant` is a new, parallel module implementing the identical
  `compute_command_grant_patterns()`/`apply_command_permission_grant()` shape for `CommandRules`
  instead of `DirRules` — same `GrantAction`, same four scopes, same cross-file-cleanup rules
  (see docs/specs/permissions.md's "Cross-file cleanup" section), just matching command argv
  patterns via `CommandPermissionsTable.matching_rules()` instead of directory containment.
* Reasoning: Once `Deny` became a first-class grid cell alongside `Allow` rather than a single
  fixed choice with no scope of its own, leaving `apply_permission_grant()` allow-only would have
  meant a persistent `Deny` decision silently doing nothing — the UI would offer a choice that
  didn't actually persist, which is worse than not offering it at all. Adding the `action`
  parameter to the existing function (rather than a separate `apply_permission_denial()`) keeps
  the "which table, which scope, which files get touched" logic in one place, since almost all of
  it — scope routing, cross-file cleanup, the live/process-config/disk triple-write — is identical
  regardless of direction; only the promotion target and the read/write-coupling asymmetry differ.
  Building `command_grant.py` as a parallel module (rather than folding `CommandRules` support
  into `grant.py` itself via a generic resource-kind parameter) matches how `CommandPermissionsTable`
  itself already mirrors `DirectoryAccessTable` as a separate, sibling implementation of the same
  `PermissionsTable[T]` abstraction (see
  [the CommandRules-mirrors-DirRules ADR](command-rules-mirror-dirrules-deny-ask-allow-evaluation.md))
  rather than a generalized single table class — the two resource kinds' matching semantics
  (directory containment vs. token-pattern matching) are different enough that a shared grant
  writer would need resource-kind branches throughout anyway, so two small, independently
  readable modules is the more direct implementation than one moderately generic one.
