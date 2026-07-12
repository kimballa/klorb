# Reconcile a persistent shell's frozen sandbox by rebuilding it when grants grow

* Date: 2026-07-12 14:10
* Question: A `bwrap` sandbox's mount namespace is fixed at launch and cannot be modified for the
  life of that `bwrap` process — there is no "add a bind mount to a running sandbox" operation. A
  `shell_lifetime="session"`/`"new"` persistent shell (`klorb.tools.bash.PersistentShell`) is, by
  design, one long-lived `bash` inside one long-lived `bwrap`, so its whole filesystem view is
  frozen at spawn. But the set of directories the session may touch is *not* frozen: the user can
  approve an `ask` mid-session, adding an `allow` to the live `SessionConfig`. A shell sandboxed at
  turn 1 physically cannot see a directory first granted at turn 6 — the write fails with
  `ENOENT`/`EACCES` inside a namespace that never had that path, even though the classification
  layer now says the command is allowed. How should the persistent shell reconcile its immutable
  namespace with the session's changing permissions?
* Answer: Reconcile-on-grow. `PersistentShell` records the `klorb.sandbox.allowed_dir_snapshot()`
  its live `bwrap` was launched with. Before each reused command,
  `BashTool._reconcile_sandbox` recomputes the current snapshot from the live tables and compares:
  if it's unchanged (the overwhelmingly common case) the command runs in the existing sandbox with
  zero extra cost and full state persistence; if it *grew*, the sandbox is stale and is rebuilt
  (`SIGKILL` the old `bwrap`, relaunch against the wider tables, replay carryable state) before the
  command runs. Neither a single static broadest-possible envelope nor an in-namespace mount helper
  is used.
* Reasoning: Within a single live session the allowed-directory set is monotonically
  non-shrinking — an interactive grant only ever *adds* an `allow` (and at most drops a
  now-redundant `ask`); nothing in a running session tightens it, and denyholes come from the
  static deny list, stable for the whole process. So the mount set only ever needs to *grow*, and
  "grow an immutable namespace" has exactly one primitive: replace it. Driving the rebuild off "the
  session's allowed-dir set grew" rather than "this command names a new path" is deliberate and
  more correct: the mount set is a function of the *tables*, not of the parsed command, so it
  correctly picks up a directory granted through some *other* tool (an `EditFile` `ask` approved
  between two `Bash` calls) and is robust to the "an approved `python -c` can `open()` a file the
  shfmt walker never saw" hole the one-shot path already documents — the sandbox binds the whole
  allowed set regardless of which paths a given command happens to name. `allowed_dir_snapshot()`
  intentionally excludes the mask set (static deny + `privileged_dirs()`), since that never grows
  and so should never trigger a rebuild. The rejected alternatives: a static broadest-possible
  envelope would have to pre-bind everything the session might ever be granted, defeating
  least-privilege; and keeping one `bash` alive while mutating its mounts from inside requires a
  custom in-sandbox init retaining `CAP_SYS_ADMIN` to call `mount(2)`, which directly contradicts
  `--cap-drop ALL` and hands the sandboxed process tree the single most dangerous capability for
  namespace escape — a large amount of bespoke, security-critical plumbing traded for avoiding an
  occasional shell respawn. Reconcile-on-grow reuses the respawn machinery the persistent shell
  already has (`shell_lifetime="new"` is the same operation) and touches nothing on the
  common, unchanged path. See
  [[rebuild-persistent-sandbox-only-when-no-live-jobs]] for how a rebuild avoids destroying live
  background work, and `klorb.sandbox`'s "Unsandboxed fallback": when `bwrap_available()` is
  `False` there is no namespace to go stale, the snapshot is `None`, and reconcile is a no-op.
