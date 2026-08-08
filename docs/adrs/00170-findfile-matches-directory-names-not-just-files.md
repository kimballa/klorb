# `FindFile` matches directory bare names too, not just file bare names

* Date: 2026-08-03 00:00
* Question: `FindFileTool` mimics `find -name`, matching a glob `pattern` against a bare name
  while `dirname` scopes where the walk starts. But the tool only ever tested `pattern` against
  entries in `walk_readable_tree`'s `file_names` list, never its `subdir_names` list — so a
  directory whose own name matched `pattern` (e.g. `pattern="*system_prompt*"` against a
  directory named `foo/bar/system_prompts.d/`) never showed up as a match, even though real
  `find -name` matches every node type by default (files, directories, symlinks) and only
  restricts to files with an explicit `-type f`. Should `FindFile` start testing directory names
  too, or should the fix instead be to match `pattern` against each entry's whole relative path
  (`find -wholename`-style) instead of its bare name?
* Answer: `FindFile` now tests `pattern` against both file and directory bare names, keeping
  bare-name matching (not whole-path matching) as the semantics. A directory whose name matches
  is returned as its own result entry, `{"dir": path}`, alongside file matches returned as
  `{"file": path}` — a plain path string no longer tells the agent which kind of match it got, so
  the two are tagged. A directory match does not stop the walk from descending into it: files
  and subdirectories inside a matched directory are still tested against `pattern` independently,
  so `system_prompts.d/` itself matching doesn't prevent `system_prompts.d/production.yaml` from
  also being reported if it independently matches some pattern. Gitignored directory names are
  handled the same way gitignored file names already were: `walk_readable_tree` grew a
  `gitignored_subdir_names` list (a directory's own name, when excluded by `.gitignore`, dropped
  from its parent's `subdir_names`) so a gitignored directory name match can still set
  `gitignored_hidden` without revealing the path, matching `FindFile`'s existing gitignored-file
  behavior. See `docs/specs/gitignore-aware-tree-walk.md`.
* Reasoning: Bare-name matching against both node types is what "recursively finds files [and
  directories] whose bare name matches a glob pattern" already promised — the file-only
  restriction was an unannounced narrowing to `find -type f -name`, not a deliberate design
  choice, and it left a real gap: a directory whose contents are exactly what the agent is
  looking for (a `*.d`-style config directory, a versioned bundle directory) was invisible unless
  the agent already guessed a file name inside it.

  We reject switching to whole-relative-path matching (`find -wholename`) as the fix.
  `fnmatch`'s `*` already matches across `/` — it has no concept of path segment boundaries the
  way shell `**` globbing does. Matching the full path means a pattern like `test_*.py` (anchored
  at the string's start under `fnmatch`) stops matching a nested `src/deep/test_foo.py`, because
  the full relative path doesn't itself start with `test_`. That breaks the single most common
  idiom this tool exists to serve — "find this basename anywhere in the tree" — in exchange for
  fixing one narrower case, and doing it correctly would mean silently prepending a wildcard or
  running two matching passes, which is a worse, more surprising contract than just closing the
  gap by testing directory names directly.
