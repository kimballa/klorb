# Scratchpad tools bypass readDirs/writeDirs entirely, rather than being pre-granted allow rules

* Date: 2026-07-09 00:00
* Question: `ReadScratchpad`/`EditScratchpad`/`SearchScratchpad` operate on
  `Session.scratchpad_path`, a file the harness itself creates and manages, not one a model
  names via a `filename`/`dirname` argument. Every other file tool (`ReadFile`, `EditFile`,
  `Grep`, ...) resolves its path argument and checks it against `readDirs`/`writeDirs` (see
  docs/specs/permissions.md) before touching disk. Should the Scratchpad tools do the same —
  e.g. by having `Session` pre-populate an `allow` rule for the scratchpad path and having each
  tool call `resolve_and_evaluate_read`/`evaluate_write` like everything else — or should they
  skip that machinery outright?
* Answer: Skip it outright. `ReadScratchpadTool`/`EditScratchpadTool`/`SearchScratchpadTool`
  read/write `klorb.tools.scratchpad.common.scratchpad_path(context)` directly, with no
  `resolve_within_workspace`/`evaluate_write`/`resolve_and_evaluate_read` call at all — the only
  gate is that `context.session` must be set (`ValueError` otherwise), since there's no
  session-scoped file to point at without one.

  `Session` does not add any `readDirs`/`writeDirs` `allow` entry for the scratchpad directory
  either — a model reaching the scratchpad file through `ReadFile`/`EditFile`/`Bash` instead of
  the dedicated tools goes through the ordinary tables exactly like any other path, and is
  denied/asked exactly as it would be for any other path outside `readDirs`/`writeDirs`. A
  first pass of this feature did add such a grant (mirroring `BashTool.
  _grant_tmp_dir_read_access`'s identical pattern for its own spilled-output directories) but it
  was reverted: several existing `test_session.py` permission tests assert the exact contents
  of `config.read_dirs.allow`/`write_dirs.allow` after a grant flow runs (e.g. `assert
  config.read_dirs.allow == [tmp_path]`), and a scratchpad-directory entry added unconditionally
  by every `Session.__init__` broke every one of them by adding an extra, unexpected list entry.
* Reasoning: `readDirs`/`writeDirs` exist to gate a *model-supplied* path against directories
  the user hasn't vetted — the whole reason `resolve_within_workspace` enforces a hard
  workspace-root boundary and `evaluate_write` requires an explicit `allow` in both tables. The
  scratchpad path is never model-supplied: it's fixed at `Session` construction time, and the
  three Scratchpad tools' schemas have no `filename`/`dirname` parameter for a model to redirect
  through. There is nothing for a permission check to protect against here — the model can't
  name a different path to reach through `EditScratchpad` no matter what it passes, since the
  tool never reads a path out of `args` at all.

  Routing through the ordinary tables anyway (via a pre-populated `allow` rule) was considered
  and rejected as pure overhead with a real downside: `resolve_within_workspace` hard-confines
  to `SessionConfig.workspace.path` unless the workspace is trusted (see
  docs/adrs/confine-file-tools-to-workspace-root.md), and the scratchpad directory —
  deliberately a fresh `tempfile.mkdtemp()`, not something under the workspace — would fail
  that boundary check in the common untrusted-workspace case regardless of any `allow` entry.
  Making the Scratchpad tools work would then require special-casing them out of that hard
  boundary anyway, which is just this same bypass restated with extra indirection in between.

  This does mean the Scratchpad tools are not equally auditable/interceptable via
  `readDirs`/`writeDirs` policy the way a model-nameable path is — a user cannot, for example,
  add a `deny` rule that blocks scratchpad writes. That's an accepted tradeoff: the scratchpad
  is harness-managed bookkeeping analogous to `tool_call_log`'s `tool-calls.log` or `BashTool`'s
  stdout/stderr spill files, not user data the permission system is meant to protect.
