# Pass `--unshare-user` explicitly because `--disable-userns` requires it

* Date: 2026-07-12 13:40
* Question: docs/plans/ready/004-bash-permissions-and-bash-tool.md's "uid/gid" note said *not*
  to pass `--unshare-user` (or `--uid`/`--gid`) to `bwrap` — reasoning that unprivileged bwrap
  creates a user namespace implicitly as needed, with an identity uid/gid map, which is exactly
  what's wanted, so passing it explicitly would be redundant. But the same plan's "bubblewrap
  args to use" list also requires `--disable-userns` (defense-in-depth against a sandboxed
  process nesting further user namespaces). When `klorb.sandbox.build_bwrap_argv` was actually
  built and run against a working `bwrap` (0.9.0, on a WSL2 dev host), `bwrap` rejected the
  combination outright: `bwrap: --disable-userns requires --unshare-user`. Which flag gives?
* Answer: Pass `--unshare-user` explicitly, alongside `--disable-userns`. Both appear in
  `build_bwrap_argv`'s namespace-flag block. The plan's "don't pass `--unshare-user`" guidance is
  overridden by direct empirical evidence it couldn't account for (it was written before any
  `bwrap` invocation could be run).
* Reasoning: The two goals — "keep an identity uid/gid mapping so files the sandboxed command
  creates in the workspace/home binds stay owned by the real user" and "disable further nested
  user namespaces" — are not actually in conflict; only the plan's assumption that the first
  goal *forbade* an explicit `--unshare-user` was. Passing `--unshare-user` explicitly still
  produces the same implicit identity mapping bwrap would have created anyway: verified directly
  by running a sandboxed `id -u` (reports the real uid, e.g. 1000, not 0 or nobody) and by having
  a sandboxed command `touch` a file in the workspace bind and confirming the resulting file is
  owned by the real user on the host, not by `root`/`nobody`. So the explicit flag costs nothing
  in ownership semantics and is the only way to satisfy `--disable-userns`'s precondition on this
  bwrap version. `--uid`/`--gid` remain unset — those would make the sandboxed process *appear*
  as a different uid than the real one, which is not a goal here (and would reintroduce the exact
  ownership-mismatch problem the plan wanted to avoid). This ADR records the deviation because
  the plan's explicit instruction was the opposite, and a future reader comparing the code to the
  plan would otherwise flag the `--unshare-user` in `build_bwrap_argv` as a mistake.
