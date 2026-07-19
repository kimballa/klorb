# Hard-code an unconditional `.git/` skip in the shared recursive tree walk

* Date: 2026-07-19 00:00
* Question: `FindFile` and `Grep` both walk a whole directory tree via the shared
  `klorb.tools.util.dir_walk.walk_readable_tree`. A git working copy's `.git/` directory holds
  thousands of internal object/pack/ref files that are never useful to a filename search or a
  text search, and enumerating them wastes context and search budget. The existing `.gitignore`
  mechanism (see `docs/adrs/find-and-grep-respect-gitignore-by-default.md`) deliberately left
  `.git/` un-special-cased, reasoning that special-casing it would make `gitignored_hidden` true
  on essentially every call in a git repo. Should the walk skip `.git/` some other way, and if so,
  how does it avoid that problem while still letting an agent search inside `.git/` on the rare
  occasion it genuinely needs to (e.g. inspecting a hook script, a packed ref, or `.git/config`)?
* Answer: `walk_readable_tree`'s recursion helper (`_walk` in
  `klorb.tools.util.dir_walk`) now skips any subdirectory literally named `.git` outright, before
  it is added to `subdirs` at all. The exclusion is hard-coded and unconditional: it does not
  consult `use_gitignore`, does not run a `.gitignore` pattern match, does not run a `readDirs`
  permission check, and does not touch `gitignored_file_names` or either tool's
  `gitignored_hidden` flag/`note`. A skipped `.git/` is simply absent from the walk's output, the
  same way `os.walk` would look if the directory didn't exist — no warning, no truncation flag,
  nothing for the agent to notice or opt out of.

  The skip only fires on a `.git` directory found *during recursion* — i.e. as a child of some
  directory the walk is already inside. The walk's own root, resolved once in
  `walk_readable_tree` before `_walk` is ever called, is never subject to this check. So a caller
  that explicitly passes `dirname="/path/to/repo/.git"` (or any path inside one) to `FindFile` or
  `Grep` searches it exactly as it would any other directory — explicitly naming a target is
  still honored as intent, matching the same principle already used for single-file `Grep` and
  for permission checks throughout the tree walk.
* Reasoning: A dedicated, tool-level skip avoids the `gitignored_hidden` problem entirely by
  living outside the gitignore mechanism: `.git/` isn't reported as "ignored," it's simply never
  part of the walk's model of the tree, so the flag keeps meaning exactly what it always meant —
  "a `.gitignore` rule hid something you might want." Hard-coding it in the shared
  `dir_walk.py` (rather than in each tool) keeps `FindFile` and `Grep` behaviorally identical
  without duplicating the check, consistent with why the walk itself is shared in the first
  place.

  We rejected three alternatives. **Extending `gitignored_hidden`/`use_gitignore` to also cover
  `.git/`** was rejected for exactly the reason the original ADR gives — it would make the flag
  fire on nearly every real-repo call, drowning out the signal it exists to carry.
  **Configuring it via `readDirs` (a default `deny` rule on every `.git/`)** was rejected because
  `readDirs` denials are visible, ask-gated, or at minimum distinguishable pruning that the walk
  already has machinery for (see
  `docs/adrs/prune-non-allow-subdirs-during-recursive-tree-walk.md`) — routing `.git/` through it
  would either need a special always-silent carve-out in that machinery too, or would risk
  surfacing an ask/deny prompt for a directory the agent never asked about, which is worse than
  today's silent skip. It's also a global default an admin could accidentally loosen or tighten,
  where the desired behavior here is invariant. **Leaving it entirely to each tool's `apply()`**
  (post-filtering entries with `.git` in the path) was rejected because both tools already share
  `walk_readable_tree` specifically to keep directory-pruning logic in one place; duplicating a
  `.git` check in two tools risks the two implementations drifting.
