# `CommandRules`' deny/ask/allow evaluation mirrors `DirRules`, reusing `PermissionsTable` as-is

* Date: 2026-07-07 10:15
* Question: `BashTool` needs a second `PermissionsTable` resource kind (after directory access)
  for shell-command argv patterns. Should it define its own bespoke evaluation order/precedence
  rules for commands, given that command matching (token-wildcard patterns) is structurally
  quite different from directory matching (path containment)?
* Answer: No — `klorb.permissions.command_access.CommandPermissionsTable` subclasses the existing
  `klorb.permissions.table.PermissionsTable[list[str]]` unchanged, implementing only `_matches()`
  for its own token-pattern semantics. `CommandRules` (`deny`/`ask`/`allow`, each
  `list[list[str]]`) is a structural copy of `DirRules`' shape (`deny`/`ask`/`allow`, each
  `list[Path]`), evaluated in the exact same fixed category order — deny first, then ask, then
  allow, with the first matching category winning regardless of how specific a lower-precedence
  rule would otherwise be.
* Reasoning: The category-order invariant (a broad `deny` always beats a narrower `allow`; see
  [[evaluate-permission-categories-deny-then-ask-then-allow]]) is about *how config layers
  combine safely*, not about what kind of resource is being matched — it holds identically
  whether the candidates are filesystem paths or argv token lists. `PermissionsTable` was already
  written generically (`Generic[T]`, an abstract `_matches(rule: T, candidate: T) -> bool`)
  specifically so a second resource kind wouldn't need to re-derive this evaluation order — see
  docs/specs/permissions.md's summary ("`TODO.md`'s 'Permissions' backlog item anticipates
  further resource kinds (bash commands, website access) built on the same abstraction"), which
  is exactly the situation this plan reaches. Reimplementing deny/ask/allow precedence a second
  time for commands would risk the two resource kinds silently drifting apart on a security-
  relevant invariant (e.g. one implementation letting rule *specificity* override category order)
  for no benefit — the only genuinely command-specific logic is the token-wildcard matching
  itself (`CommandPermissionsTable._matches`), which is exactly the one method the abstraction
  asks a subclass to supply. `SessionConfig.command_rules` is merged across config layers the
  same way `read_dirs`/`write_dirs` already are too — concatenation, not scalar replacement (see
  `klorb.process_config.load_process_config`) — for the identical reason: a stricter rule from
  any layer must never be discardable by a looser rule from another, regardless of resource kind.
