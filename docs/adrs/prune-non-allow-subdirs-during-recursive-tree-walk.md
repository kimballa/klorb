# Prune a non-`"allow"` subdirectory during a recursive tree walk, rather than aborting or asking

* Date: 2026-07-04 01:19
* Question: `GrepTool` and `FindFileTool` (`TODO.md`'s "More tools" item) both need to
  recursively search a whole directory tree, unlike the existing single-directory/single-file
  tools (`ListDir`, `ReadFile`, ...), each of which resolves and checks exactly one path per
  call via `klorb.permissions.workspace.resolve_and_evaluate_read()` and simply raises
  (`PermissionError` for `"deny"`, `PermissionAskRequired` for `"ask"`) if that one path isn't
  `"allow"`. A large tree being walked recursively will often contain a mix of directories with
  different verdicts — most of the tree readable, one narrow subtree `readDirs.deny`'d (a
  `node_modules/`, a customer-data fixture directory, ...). Should a `"deny"`/`"ask"`
  subdirectory encountered mid-walk raise and abort the entire search (matching the
  single-directory tools' behavior exactly), or should it be excluded from the walk and the
  search continue over the rest of the tree?
* Answer: Exclude it (prune the subtree) and keep going, but only *inside* the walk — the walk's
  own root (`dirname` as given to `Grep`/`FindFile`) is still resolved and checked exactly like
  `ListDir`'s `dirname`, raising `PermissionError`/`PermissionAskRequired` if *that* path isn't
  `"allow"`. Once inside an already-permitted root, every subdirectory the walk is about to
  descend into gets its own independent `resolve_and_evaluate_read()` call
  (`klorb.tools.dir_walk._is_descendable()`); anything other than `"allow"` — `"deny"`, `"ask"`,
  or even a `PermissionError` from a symlink that resolves outside the workspace root — removes
  that subdirectory from the walk's `subdir_names` and skips recursing into it, silently, with
  no exception raised and no partial-result flag set.
* Reasoning: A single explicit tool-call target (`ListDir("secret")`, `ReadFile("secret/f.txt")`)
  is user intent — the model asked for exactly that path, so failing loudly when it's not
  permitted is the right, informative outcome. A recursive search's `subdir_names` were never
  requested individually; they're an implementation detail of walking a tree the model asked to
  search as a whole. Aborting the entire `Grep`/`FindFile` call because one subdirectory
  somewhere under a large root is restricted would make the tools nearly unusable on any
  workspace with even one denied path (a common case — see `docs/specs/permissions.md`'s
  `${workspace_root}/.klorb/` privileged-path protection, which every recursive search already
  runs into on every call). Treating `"ask"` the same as `"deny"` here (rather than raising
  `PermissionAskRequired` per subdirectory) avoids a bulk search interactively prompting once per
  restricted subtree, which would be surprising and disruptive for what the model expects to be
  one search; a directory that genuinely needs interactive confirmation before being searched can
  still be searched directly via a narrower `dirname` naming it, which goes through the same
  root-level check as any other explicit target.

  This does mean `Grep`/`FindFile` can silently return fewer matches than actually exist on disk
  when part of the tree is restricted, with no signal distinguishing that from "no matches in
  that subtree." This is an accepted, deliberate trade-off given the goal (bulk search
  usability); a future revision could add an `excluded_dirs` field to the result if that
  ambiguity becomes a real problem in practice.
