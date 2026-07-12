# Mask sandbox denyholes with `--tmpfs`, not empty-placeholder ro-binds

* Date: 2026-07-12 13:55
* Question: `build_bwrap_argv` binds the whole home directory read-write (so toolchains under it
  work) and then has to hide the sensitive subdirectories inside it (`~/.ssh`, `~/.aws`, ... — the
  `readDirs.deny` list — plus `privileged_dirs()` like `<workspace>/.klorb` and the klorb
  config/data/state dirs). docs/plans/ready/004-bash-permissions-and-bash-tool.md described masking
  each such path with an *empty placeholder directory* bound over it (`--ro-bind <empty_dir>
  <path>`), and spent a whole "Mount-point cleanup" section on the machinery that requires: the
  placeholder has to exist on the host as a bind source, persists after the sandbox exits, and so
  needs proactive per-invocation cleanup plus an `atexit` sweep. Is that placeholder-and-cleanup
  approach actually necessary, or is there a mask primitive that needs no host-side source at all?
* Answer: Mask every denyhole with `--tmpfs <path>` instead. `build_bwrap_argv` emits one
  `--tmpfs` per existing `dirs.mask` directory, applied last (after the home/allowed binds) so it
  wins over whatever bind would otherwise expose it. No host-side placeholder directory is created,
  so the entire "Mount-point cleanup" machinery the plan called for — proactive per-invocation
  removal plus an `atexit` handler — is simply not needed and does not exist.
* Reasoning: `--tmpfs <path>` mounts a fresh, empty, in-sandbox tmpfs over the target, which is
  exactly the "present but empty" shape masking wants, and it requires no source path on the host
  the way `--ro-bind <empty_dir> <path>` does — so there is nothing left on the host to clean up
  after the sandbox tears down. Verified directly against `bwrap` 0.9.0 that this works for all the
  cases that matter: over a child of the read-write home bind (`~/.ssh` — its real contents become
  invisible), over a child of the read-only `/etc` bind (`/etc/ssh`), and over a path that doesn't
  exist on the host at all (bwrap creates the mount point). The plan itself already listed
  "`--ro-bind` *or* `--tmpfs` over" as acceptable for these denyholes, so choosing `--tmpfs`
  outright is within its intent, not a reversal of it — it just also lets the cleanup section be
  dropped entirely. The one behavioral difference is that a `--tmpfs` mask is writable inside the
  sandbox (writes land in the throwaway tmpfs and vanish at teardown) rather than read-only; this
  is harmless, since the whole point is that the *real* directory's contents are unreachable, and
  nothing the command writes into the empty overlay escapes the sandbox. The `dirs.mask` set is
  derived from the same `readDirs.deny` and `privileged_dirs()` source of truth the file tools
  already honor (see `klorb.sandbox.compute_sandbox_dirs`), so the sandbox and the classification
  layer mask the same paths rather than maintaining two independent denylists.
