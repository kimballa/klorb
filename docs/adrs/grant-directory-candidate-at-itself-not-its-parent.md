# A no-match grant for a candidate that is itself a directory is recorded at that directory, not its parent

* Date: 2026-07-09 15:00
* Question: `compute_grant_paths()`'s no-match fallback
  (docs/adrs/grant-unmentioned-paths-at-containing-directory.md) records a fresh grant at
  `candidate.parent` whenever nothing in `readDirs`/`writeDirs` mentions `candidate` yet. That
  ADR was written entirely in terms of file candidates (`ReadFile`/`EditFile`/an explicit `>`/`<`
  bash redirect), where "the containing directory" and "the parent of the accessed path" are the
  same thing. Now that `BashTool`'s `IMPLICIT_READ_COMMANDS` includes `ls`
  (docs/specs/bash-tool-and-command-permissions.md), a candidate reaching this fallback can
  itself already be a directory (`ls src/` with nothing in `readDirs` covering `src/` yet). Should
  that case still use `candidate.parent`, or `candidate` itself?
* Answer: `candidate` itself, when `candidate.is_dir()` is true; `candidate.parent` otherwise,
  unchanged from the file-leaf case. `compute_grant_paths()`
  (`klorb.permissions.grant`) branches on this directly.
* Reasoning: The original ADR's whole point was that a grant should land at the directory-scoped
  unit `DirectoryAccessTable` already operates on, rather than one level narrower (a single file).
  Taking `.parent` unconditionally over-corrects when `candidate` is already that directory-scoped
  unit: granting `ls src/`'s "always allow" at `src/`'s parent hands out read access to everything
  else living alongside `src/` (a sibling directory the model never touched and the prompt never
  named) to cover a request that only ever needed `src/` itself. `PermissionAskScreen`'s modal
  copy names whichever path this function returns, so the fix also keeps that copy accurate — it
  was already describing `candidate.parent` as "the directory being granted" for a file, and would
  otherwise have silently mis-described a directory candidate's own grant as its parent instead.
