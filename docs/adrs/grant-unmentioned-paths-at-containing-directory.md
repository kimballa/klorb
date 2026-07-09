# A grant for a path no rule ever mentioned is recorded at its containing directory, not the exact file

* Date: 2026-07-03 18:25
* Question: When an interactive Allow grant's candidate path matched no existing `ask` rule at
  all (the "never mentioned" case — e.g. a bare write interrogation whose `readDirs`/`writeDirs`
  are simply empty, normalizing to `"ask"` by default), what path should the fresh `allow` entry
  be recorded at: the exact file the tool touched, or that file's containing directory?
* Answer: The containing directory (`path.parent`). `klorb.permissions.grant
  .compute_grant_paths()` falls back to `[candidate.parent]` whenever
  `PermissionsTable.matching_rules("ask", candidate)` returns nothing for the relevant table(s).
  `PermissionAskScreen`'s modal copy states this explicitly — naming the directory being granted
  — before the user picks a scope, since it's granting more than the single file visibly
  triggering the prompt.
* Reasoning: `readDirs`/`writeDirs` are directory-scoped rules by design (`DirectoryAccessTable`
  matches by ancestor-or-self containment, not per-file globs — see docs/specs/permissions.md's
  "Known risks" section on the current lack of file-level rule support). Recording a fresh grant
  at the single accessed file, rather than its directory, would silently work *against* that
  design: a write interrogation's no-match default is `"ask"`, not a permissive fallback (see
  [the write-verdict ADR](write-verdict-is-stricter-of-read-and-write-tables.md)), so the very
  next *different* file the model tries to touch in that same directory would trigger a fresh
  `"ask"` prompt all over again — one file at a time, indefinitely, for what a user would
  reasonably expect to be "work in this directory" granted once. Granting at the directory level
  matches how the rest of the permission system already treats `readDirs`/`writeDirs` entries,
  and matches user expectations for what "allow access here" means in practice.

  This was a deliberate choice over the more conservative alternative (grant only the exact file,
  minimizing the blast radius of any one grant): the user explicitly weighed in favor of directory
  scope during this feature's design, on the condition that the modal's copy make the actual
  granted scope unambiguous before the choice is made — the UI requirement this ADR's "Answer"
  section calls out is not optional decoration, it's the mitigation for granting broader than the
  single file in view. This only applies to the no-match fallback; when an existing `ask` rule
  did match, its own path is promoted instead — see
  [the promotion ADR](promote-matched-ask-rule-path-not-candidate-on-grant.md), which can be
  broader or narrower than a bare directory depending on how that rule was originally written.

  This ADR's own "containing directory" framing assumed `candidate` is always a file leaf; see
  [the directory-candidate grant ADR](grant-directory-candidate-at-itself-not-its-parent.md) for
  the case where `candidate` is already a directory in its own right.
