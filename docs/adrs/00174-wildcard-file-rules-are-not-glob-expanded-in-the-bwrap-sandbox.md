# A wildcard `readFiles`/`writeFiles` rule is not glob-expanded when building the bwrap mount plan

* Date: 2026-08-07
* Question: `FileAccessTable` (`klorb.permissions.file_access`) now matches a `readFiles`/
  `writeFiles` entry containing `*` by pattern (see
  docs/adrs/00173-file-rule-wildcard-star-matches-any-characters-including-slash.md), not just exact
  path equality. `klorb.sandbox.compute_sandbox_dirs` independently builds `bwrap`'s mount plan
  from the same `FileRules`, bind-masking each `readFiles.deny` entry that `.exists()` on disk
  with `--ro-bind /dev/null <path>` (see docs/specs/bash-tool-and-command-permissions.md's
  "Individual files"). A literal path containing `*` essentially never `.exists()`, so a
  wildcard `deny` entry (e.g. `*.pem`) was silently dropped from the mount plan even before this
  ADR — an accident of `Path.exists()`'s behavior, not a considered decision. Now that wildcard
  rules are a real, documented feature, should `compute_sandbox_dirs` glob-expand each wildcard
  `readFiles.deny` pattern against the directories it's about to bind, and mask every match
  individually, so a sandboxed shell command can't read a file the wildcard would deny the
  agent's own file tools?
* Answer: No — a rule containing `*` is explicitly skipped by `compute_sandbox_dirs` (via
  `klorb.permissions.file_access.is_wildcard_rule`) in every category, not glob-expanded. The
  previous accidental skip becomes an intentional, logged one
  (`logger.debug` in `existing_file_rules`), but the resulting mount plan is unchanged: only a
  concrete (non-wildcard) `readFiles`/`writeFiles` entry ever becomes a `mask_files`/`read_files`/
  `write_files` bind.
* Reasoning: Expanding a wildcard against the sandbox's bound directories would mean walking
  every directory in `read_write`/`read_only` (in the common case, the whole `$HOME` and
  workspace trees) on every sandbox (re)build, to find every file each pattern could match —
  expensive on a large `$HOME`, and re-run on every reconcile-on-grow rebuild
  (`klorb.tools.bash`'s persistent-shell path). It also wouldn't actually close the gap it's
  meant to close: a file created *after* the walk (a fresh `.pem` dropped into the workspace
  mid-session) would still be unmasked until the next rebuild, the same class of TOCTOU gap
  already tracked in `TODO.md`'s "Permissions" section for path-string re-resolution generally —
  spending real complexity and per-rebuild cost to make the gap merely usually-smaller isn't a
  good trade. `docs/adrs/00064-bubblewrap-is-defense-in-depth-not-a-classifier-substitute.md` already
  establishes that `bwrap` is a *second* layer, not a substitute for the classification layer
  (`FileAccessTable`, which enforces every wildcard `deny` exactly, with no such gap, against
  every `ReadFile`/`EditFile`/`CreateFile`/`ReplaceAll` call): a wildcard `readFiles.deny` entry
  still fully protects the file from the agent's own tools. What it does not do, both before and
  after this decision, is stop a `bwrap`-sandboxed shell command from reading that file directly
  (`cat key.pem`) — a pre-existing asymmetry for concrete `readFiles.deny` entries too, whenever
  the file didn't exist yet at sandbox-build time; wildcard entries simply inherit the same
  known limitation rather than getting a bespoke partial fix. `klorb.resources.default-config.json`
  ships `*.pem` as a `readFiles.deny` entry with this understood: it hardens the agent's own file
  tools against reading a private key by name pattern, and is defense-in-depth (not a guarantee)
  against a sandboxed shell command doing the same.
