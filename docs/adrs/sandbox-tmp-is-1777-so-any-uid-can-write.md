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

## Reasoning

The sandbox launches the command with `cwd = workspace_root` (the user's checkout) and mounts a
minimal filesystem. Crucially, `/tmp` ends up being the *only* writable directory in Python's
standard temp-dir search path inside the sandbox:

* `--tmpfs /var` shadows `/var/tmp` with a fresh empty tmpfs, so `/var/tmp` no longer exists.
* There is no `/usr/tmp` under the read-only `/usr` bind.

`tempfile.gettempdir()` tries `$TMPDIR`, `$TEMP`, `$TMP`, then `/tmp`, `/var/tmp`, `/usr/tmp`, and
finally `os.getcwd()`. On a normal host, an unwritable `/tmp` is caught by `/var/tmp`; inside the
sandbox that cushion is gone, so an unwritable `/tmp` sends `gettempdir()` straight to
`os.getcwd()` — the workspace root — and every `mkstemp`/`mkdtemp`/pytest `tmp_path` then scatters
directories (`pytest-of-<user>`, `klorb-bash-*`, …) into the user's checkout. When klorb is used
on its own source tree, that also breaks tests whose `tmp_path` is now nested under the repo's
`.klorb/`.

Whether `/tmp` is writable with the default `0755` depends entirely on whether the sandbox's user
namespace maps the command to the same uid that owns the tmpfs root inode. On some hosts it does
(the tmpfs is owned `uid=1000` and the command runs as `1000` — writable, no symptom). On others
the userns leaves `/tmp` owned by (namespace) root while the command runs unprivileged, making a
`0755` `/tmp` read-only to the command and triggering the fallback above. This host-dependent
behavior is exactly the kind of "works on my machine" fragility we don't want in a security
boundary.

A real `/tmp` is `1777` (sticky, world-writable) precisely so any user can create files in it
while the sticky bit stops them from deleting each other's. Mounting the sandbox's `/tmp` the same
way makes it writable by *any* uid the namespace might use, removing the dependence on tmpfs
ownership. `--perms` applies only to the immediately following mount operation, so it is emitted
directly before `--tmpfs /tmp` and does not affect the subsequent `--tmpfs /var` (left at the
default; `/var` is internal scaffolding the command is not expected to write).

### Rejected alternatives

* **Set `TMPDIR=/tmp` in the sandbox env instead.** Doesn't help: if `/tmp` itself is unwritable,
  `tempfile` discards the `$TMPDIR` candidate and continues down the same list to `cwd`.
* **Re-add a writable `/var/tmp` (`--dir /var/tmp`).** Restores one fallback but leaves `/tmp`
  itself read-only, so anything that writes to `/tmp` by name still fails. Fixing `/tmp` directly
  is the root-cause fix; a `/var/tmp` cushion is at best defense-in-depth on top of it.
* **Diagnose and pin the userns uid mapping so the tmpfs is always owned by the command's uid.**
  More invasive, and `1777` makes the ownership irrelevant, which is simpler and matches real-`/tmp`
  semantics.

### Note on a superseded diagnosis

An earlier investigation (`tmp-dir-issues.md`) attributed the repo pollution to the host's real
`/tmp` being mounted read-only *on top of* bwrap's `--tmpfs /tmp`, shadowing it. That is not
reproducible: bwrap's `--tmpfs /tmp` wins for the sandboxed process even when the invocation is
nested under an outer read-only `/tmp`. The real cause is the tmpfs-ownership/mode issue described
above combined with the missing `/var/tmp` fallback, not a shadowing mount.
