# Bind the workspace `.klorb/` dir in the sandbox (ro, or rw after escalation) instead of masking it

* **Date:** 2026-07-17
* **Question:** Inside the `bwrap` sandbox, `<workspace>/.klorb` was hidden with an empty
  `--tmpfs` mask (it's a `privileged_dirs()` entry). That made a sandboxed `git status` report the
  managed files klorb checks in there — `klorb-config.json`, etc. — as *deleted*, because they
  exist in the git index but not in the masked working tree. How should the sandbox expose that
  directory instead?

## Answer

Bind `<workspace>/.klorb` rather than mask it:

* **read-only by default** — the sandboxed shell sees the real, tracked files (so `git status` is
  clean) but cannot rewrite klorb's own managed settings;
* **read-write once the session has a `scope="workspace"` escalation** — the same
  `EscalatePrivileges` grant that already lifts the file tools' privileged-path deny on that dir.

Concretely: `compute_sandbox_dirs` now takes `approved_scopes`, drops `<workspace>/.klorb` from the
`mask` set, and records it in two new `SandboxDirs` fields (`workspace_config_dir`,
`workspace_config_writable`). `build_bwrap_argv` emits the ro/rw bind *after* the directory masks
and after the whole-workspace bind, so the ro/rw decision wins for that subtree. The writable form
is included in `allowed_dir_snapshot`, so an escalation grows the reconcile-on-grow set and the
persistent shell is rebuilt with `.klorb` now writable. The process-wide `KLORB_*_DIR` locations
stay masked exactly as before — `"workspace"` scope only ever covers the workspace's own dir.

## Reasoning

The mask was collateral damage from treating every `privileged_dirs()` entry identically. The
*intent* of privileging `.klorb` is that the model's tools (and shell) must not **read secrets from
or write** klorb's own managed state — not that the directory should appear not to exist. A
read-only bind satisfies the real goal (managed settings can't be tampered with from the shell)
while removing the surprise: the whole point of a coding agent's sandbox is that ordinary
dev commands like `git status`/`git add -A`/`git stash` behave normally, and a working tree that
looks like someone `rm`'d a tracked file breaks that badly (a naive `git commit -am` would even
record the phantom deletion).

Read-only, not read-write, is the default because `.klorb` holds klorb-managed configuration that a
command shouldn't silently rewrite; the existing `scope="workspace"` escalation is exactly the
"user has decided the agent may touch klorb's own project settings" signal, so tying the writable
bind to it keeps the sandbox mount policy one source of truth with the file-tool permission layer
(`privileged_dirs(approved_scopes)`) rather than inventing a second, independently-drifting rule.

Alternatives considered and rejected:

* **Keep masking, and special-case git.** No clean seam — the corruption is at the filesystem view,
  not in git; anything reading the working tree (build tools, linters, `ls`) sees the same hole.
* **Always bind read-write.** Drops the protection on klorb's managed settings for the common,
  un-escalated case; the whole reason `.klorb` is privileged is to keep tools out of it by default.
* **A brand-new config knob for the mode.** Redundant with `approved_scopes["workspace"]`, which
  already means precisely "the agent may act on the workspace's own `.klorb`."

See docs/specs/bash-tool-and-command-permissions.md's "Sandboxing" section and
docs/adrs/mask-sandbox-denyholes-with-tmpfs-not-placeholder-binds.md (the masking idiom this
carves one exception out of).
