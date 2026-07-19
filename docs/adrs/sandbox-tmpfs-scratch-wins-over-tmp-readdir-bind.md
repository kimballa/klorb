# The sandbox's disposable `/tmp`/`/var` tmpfs wins over a `readDirs`/`writeDirs` bind of the same path

* **Date/time:** 2026-07-12 21:10 local

## Question

`build_bwrap_argv()` derives its mount binds from the session's `readDirs`/`writeDirs` permission
tables (one source of truth with the file tools). klorb's default `readDirs.allow` includes
`/tmp`. It also mounts a fresh, disposable `--tmpfs /tmp` (and `--tmpfs /var`) for scratch space.
Those two intents collide: what should the sandbox's `/tmp` be — the host's `/tmp` (bound in from
the read rule) or the fresh scratch tmpfs?

## Answer

The scratch tmpfs wins. A directory bind whose target is *exactly* `/tmp` or `/var` (the
disposable tmpfs mount points, `DISPOSABLE_TMPFS_DIRS`) is dropped in `build_bwrap_argv()` rather
than emitted. A bind of a directory *under* `/tmp`/`/var` is still honored and lands on top of the
tmpfs, as before.

## Reasoning

Because `/tmp` is in `readDirs.allow`, `compute_sandbox_dirs()` placed it in
`SandboxDirs.read_only`, and `build_bwrap_argv()` emitted `--ro-bind /tmp /tmp` *after* the
`--tmpfs /tmp`. bwrap applies mounts in order, so the read-only bind of the host's real `/tmp`
landed on top of and completely shadowed the scratch tmpfs. Observed directly by driving the real
`BashTool` (`overlay` mount `8:32 /tmp /tmp ro ext4 /dev/sdc` stacked on the tmpfs): inside the
sandbox `/tmp` was read-only, `touch /tmp/x` failed with "Read-only file system", and
`tempfile.gettempdir()` fell through its candidate list (`/tmp` unwritable, `/var/tmp` and
`/usr/tmp` absent because `--tmpfs /var` shadows the former and `/usr` is a read-only bind) all the
way to `os.getcwd()` — the workspace root — so `mkstemp`/`mkdtemp` and pytest's `tmp_path` created
`klorb-bash-*`, `pytest-of-*`, etc. directly inside the user's checkout. When klorb was run on its
own source, that also broke the `find_workspace_root` tests whose `tmp_path` was now nested under
the repo's `.klorb/`.

The sandbox's `/tmp` is meant to be private, writable, disposable scratch — not a view of the
host's `/tmp`. So the tmpfs is the intended semantics for that exact path and must not be clobbered
by a bind that happens to name it. Dropping only the *exact* `/tmp`/`/var` targets preserves the
existing (and deliberate — see the mount-ordering comment in `build_bwrap_argv()`) behavior that a
bind of a subdirectory under `/tmp` lands on top of the tmpfs.

### Rejected alternatives

* **Remove `/tmp` from the default `readDirs.allow`.** That changes the *file tools'* behavior
  (whether the agent may read `/tmp/...` paths at all) for an unrelated reason, and any user config
  or future default that re-adds `/tmp` would reintroduce the sandbox bug. The conflict is
  specific to the sandbox mount layer, so it is resolved there.
* **Emit the disposable tmpfs mounts *after* the binds.** Would fix the clobber for `/tmp` but
  invert the intended ordering for the documented "workspace root beneath a system temp dir" case,
  where a bound directory under `/tmp` must land on top of the tmpfs, not be wiped by it.

### Relationship to the `1777` change

`docs/adrs/sandbox-tmp-is-1777-so-any-uid-can-write.md` (mounting the scratch `/tmp` world-writable
and sticky) is retained as defense-in-depth: it guarantees the tmpfs is writable regardless of the
userns uid mapping. It was **not** the trigger for the observed repo pollution — the read-only
host-`/tmp` bind was — and on its own does not fix it, since a bind stacked on top of the tmpfs
overrides the tmpfs's permissions entirely. The two changes are complementary: this one keeps the
tmpfs from being shadowed at all; the `1777` one keeps the (now-unshadowed) tmpfs writable across
uid mappings.

### Note on a superseded diagnosis

An earlier investigation (`tmp-dir-issues.md`) attributed the read-only `/tmp` to the *outer*
host/WSL2 mounting its real `/tmp` on top of bwrap's tmpfs via mount propagation. That is wrong:
the read-only overlay is emitted by klorb's *own* argv (`--ro-bind /tmp /tmp`, from `/tmp` being in
`readDirs.allow`), reproducible with nothing mounted from outside. bwrap's `--tmpfs /tmp` is not
shadowed by any outer mount.
