# A grant for a matched `ask` rule is recorded at that rule's own path, not the resolved candidate

* Date: 2026-07-03 18:10
* Question: When the user answers an interactive `"ask"` prompt with a persistent Allow, and the
  candidate path matched one or more existing `readDirs`/`writeDirs` `ask`-category rules (rather
  than nothing at all), what path should the new `allow` entry be recorded at — the resolved
  candidate path the tool actually touched (e.g. `${workspace}/src/main.py`), or the matched
  `ask` rule's own path (e.g. `${workspace}/src`, if that's what the `ask` entry covered)?
* Answer: The matched rule's own path. `klorb.permissions.grant.compute_grant_paths()` calls the
  new `PermissionsTable.matching_rules("ask", candidate)` (`klorb.permissions.table`) — which
  reports *which* rule(s) matched, not just that the category did — and promotes each matched
  rule from `ask` to `allow` at that rule's own, already-canonicalized path, removing it from
  `ask` in the same operation. Only when nothing matched at all does the grant fall back to a
  freshly-computed path (see
  [the grant-granularity ADR](grant-unmentioned-paths-at-containing-directory.md) for that case).
* Reasoning: An `ask` rule frequently covers more than the one file that happened to trigger the
  prompt — e.g. `readDirs.ask = ["~/maybe"]` is meant to gate the whole `~/maybe` subtree, not
  just whichever file inside it the model touched first. Recording the grant at the narrower
  resolved candidate instead would only silence the prompt for that one file: the very next
  *different* file under `~/maybe` would trigger a fresh `"ask"` evaluation, and since the
  original broad `ask` rule is still present, it would still shadow the new narrow `allow` entry
  (category order always checks `ask` before `allow`, regardless of specificity — see
  [the category-order ADR](evaluate-permission-categories-deny-then-ask-then-allow.md)), meaning
  the user would be asked again and again, one file at a time, forever. Promoting the matched
  rule's own path instead grants exactly the breadth the original `ask` rule already implied,
  and removing it from `ask` in the same step is what actually stops it from continuing to
  dominate the new `allow` entry.

  This is also why the removal is unconditional in memory regardless of persistence scope: an
  `ask` rule left in place, even briefly, continues to win over any `allow` entry added
  alongside it for an overlapping path, so simply *adding* an `allow` without removing the
  matched `ask` accomplishes nothing on its own.
