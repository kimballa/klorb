# A write access's grant unions matched `ask` rules across both `readDirs` and `writeDirs`, applied identically to both tables

* Date: 2026-07-03 18:30
* Question: `evaluate_write()` takes the stricter of independently-evaluated `readDirs` and
  `writeDirs` verdicts (see
  [the write-verdict ADR](write-verdict-is-stricter-of-read-and-write-tables.md)), so a write
  access's `"ask"` verdict could originate from a matching `ask` rule in `readDirs`, in
  `writeDirs`, or both — and the two tables' matching rules can cover different, non-identical
  paths (e.g. a broad `readDirs.ask = ["~/project"]` alongside a narrower
  `writeDirs.ask = ["~/project/src"]`). When the user grants Allow for a write access, which
  rule's path should be promoted, and does the answer differ between the two tables?
* Answer: One unioned set, applied identically to both. `klorb.permissions.grant
  .compute_grant_paths()` collects `readDirs`' own matched `ask` rules first (always), then, only
  for a write access (`is_write=True`), adds in `writeDirs`' matched `ask` rules too (deduped).
  That single combined list is then used as `granted_paths` for *both* tables: appended to
  `readDirs.allow` and `writeDirs.allow` alike, and used to decide which entries to remove from
  each table's own `ask` list — independently per table (a `readDirs.ask` entry is only ever
  removed from `readDirs`, never from `writeDirs`, and vice versa), but against the same target
  path set. A read-only access never touches `writeDirs` at all — matching
  `resolve_and_evaluate_read()`'s own read-only semantics — so this union only ever happens for
  a write access.
* Reasoning: `evaluate_write()` requires an explicit `allow` in *both* tables for write to
  succeed at all, so a write-scoped grant that only promoted, say, `writeDirs`' own narrower
  matched rule while leaving `readDirs`' broader one in `ask` would still shadow the just-granted
  write access on the very next call — `readDirs` alone would still say `"ask"`, and
  `evaluate_write()`'s stricter-of-both-tables merge would immediately re-cap the verdict back
  down. Using one combined path set for both tables' promotion keeps the whole grant internally
  consistent in one step, rather than requiring two separately-reasoned-about promotions that
  could disagree on scope. Granting the *union* (rather than, say, the intersection, or only
  `writeDirs`' own matches) errs toward the same "don't ask again for a sibling path" reasoning
  as [the grant-granularity ADR](grant-unmentioned-paths-at-containing-directory.md): a narrower
  promotion would leave the broader `readDirs` rule in place, still shadowing nearby paths that
  rule was written to cover.

  The alternative considered — promoting only whichever table's `ask` rule was narrowest (closest
  to the exact candidate) — was rejected as needless complexity for a rare edge case (two
  differently-scoped `ask` rules covering the same access from different tables), and because
  "narrowest" has no obviously correct definition when the two rules aren't nested (e.g. sibling
  directories that both happen to contain the candidate via different ancestor chains — not
  possible for directory containment specifically, but the principle generalizes poorly). Union
  is unambiguous and requires no such judgment call.
