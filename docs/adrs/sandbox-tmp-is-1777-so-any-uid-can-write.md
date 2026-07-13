# Sandbox `/tmp` is mounted 1777 so any uid inside the userns can write it

* **Date/time:** 2026-07-12 20:05 local

## Question

The `bwrap` sandbox `BashTool` builds (`klorb.sandbox.build_bwrap_argv`) mounts a fresh
`--tmpfs /tmp` for scratch space. bwrap's default permissions for a `--tmpfs` are `0755`, which
makes the mount writable *only* by the uid that owns its root inode. Should we keep the default,
or force a specific mode?

## Answer

Mount it `--perms 1777 --tmpfs /tmp` — world-writable plus the sticky bit, exactly like a real
system `/tmp` — instead of relying on bwrap's default `0755`.

This is **insurance / defense-in-depth**, not the fix for the observed repo-pollution bug. That
bug was caused by a read-only host-`/tmp` bind stacked on top of the tmpfs; see
`docs/adrs/sandbox-tmpfs-scratch-wins-over-tmp-readdir-bind.md` for the actual root cause and fix.

## Reasoning

The sandbox launches the command with `cwd = workspace_root` (the user's checkout) and leaves
`/tmp` as the *only* writable directory in Python's standard temp-dir search path: `--tmpfs /var`
shadows `/var/tmp` with an empty tmpfs, and there is no `/usr/tmp` under the read-only `/usr` bind.
`tempfile.gettempdir()` tries `$TMPDIR`, `$TEMP`, `$TMP`, then `/tmp`, `/var/tmp`, `/usr/tmp`, and
finally `os.getcwd()`. On a normal host an unwritable `/tmp` is caught by `/var/tmp`; inside the
sandbox that cushion is gone, so *any* condition that makes `/tmp` unwritable sends `gettempdir()`
straight to the workspace root and scatters temp dirs into the checkout.

Given how load-bearing `/tmp`'s writability is, it should not depend on whether the sandbox's user
namespace happens to map the command to the same uid that owns the tmpfs root inode. With bwrap's
default `0755`, a tmpfs owned by (namespace) root is read-only to an unprivileged command; the
mapping is environment-dependent and exactly the kind of "works on my machine" fragility we don't
want near a security boundary. A real `/tmp` is `1777` (sticky, world-writable) precisely so any
user can create files in it while the sticky bit stops them from deleting each other's. Mounting
the sandbox's `/tmp` the same way makes it writable by *any* uid the namespace might use.

`--perms` applies only to the immediately following mount operation, so it is emitted directly
before `--tmpfs /tmp` and does not affect the subsequent `--tmpfs /var` (left at the default;
`/var` is internal scaffolding the command is not expected to write).

### Rejected alternatives

* **Set `TMPDIR=/tmp` in the sandbox env instead.** Doesn't help: if `/tmp` itself is unwritable,
  `tempfile` discards the `$TMPDIR` candidate and continues down the same list to `cwd`.
* **Do nothing here and rely only on not clobbering the tmpfs.** Not clobbering the tmpfs (the
  companion ADR) is the actual fix for the reported bug, but `0755` would still leave `/tmp`'s
  writability dependent on the userns uid mapping; `1777` removes that dependency cheaply.
