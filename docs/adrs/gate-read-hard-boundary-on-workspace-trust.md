# Gate ReadFile's hard workspace-root boundary on a not-yet-settable `is_workspace_trusted` flag

* Date: 2026-07-02 01:35
* Question: `EditFile`/`ReplaceAll`/`CreateFile` are hard-confined to `workspace_root` via
  `resolve_within_workspace()` — a boundary no `writeDirs` config entry can widen — on top of
  which the new `writeDirs` table narrows access further (see
  `docs/adrs/confine-file-tools-to-workspace-root.md` and
  `docs/adrs/evaluate-permission-categories-deny-then-ask-then-allow.md`). Should `ReadFile`
  get that same hard boundary, or should it be governed purely by the `readDirs`/`writeDirs`
  table, letting `readDirs.allow` reach outside `workspace_root`? The latter was requested (the
  spec's own example config lists paths like `/yolo` that don't obviously belong to any one
  project's workspace), but it reopens a real risk: `.klorb/klorb-config.json` — the
  *least*-trusted config layer, since it arrives via whatever `cwd` an untrusted, cloned
  repository puts a user in — participates in the exact same flat, concatenated `readDirs.allow`
  list as the user's own trusted home config. Without a hard boundary, a hostile repository
  could ship `readDirs.allow: ["~/.ssh"]` and have it silently honored the moment the user runs
  klorb from inside it.
* Answer: Both — gated by a new `ProcessConfig.is_workspace_trusted: bool` field, default
  `False`, **deliberately absent from `PROCESS_KEY_MAP`** so no `klorb-config.json` layer (at
  any precedence, including the trusted `/etc`/user layers) can set it. When untrusted (the
  default, and today the *only* value reachable by any code path outside tests —
  `is_workspace_trusted` is not yet settable anywhere in production code), `ReadFile` gets
  `resolve_within_workspace()`'s hard boundary, exactly like the write tools, with the
  `readDirs`/`writeDirs` six-step chain narrowing further within it. When trusted (reserved for
  a future, not-yet-built "trust this workspace" flow — see `TODO.md`'s "project bootstrapping"
  item), `ReadFile` drops the hard boundary and is governed purely by the table, so
  `readDirs.allow` can reach outside `workspace_root`.
* Reasoning: This closes the self-granted-read-access risk for every workspace klorb can
  actually reach today, without abandoning the table-only design the fuller vision calls for.
  Since nothing sets `is_workspace_trusted = True` outside of tests in this codebase, the risk
  above requires a *future* feature (an explicit, presumably interactive, per-workspace trust
  decision — never a config file, for the same reason `is_workspace_trusted` has no on-disk key
  at all: a project trusting itself via its own config would defeat the point) to even become
  reachable. When that future flow exists, the risk returns in exactly the form originally
  identified, and the mitigation is unchanged: a user- or `/etc`-level `readDirs.deny` for a
  sensitive path (`~/.ssh`, `~/.aws`, etc.) always outranks anything a project layer can add,
  by the invariant in `docs/adrs/evaluate-permission-categories-deny-then-ask-then-allow.md`.
  This ADR's job is to make sure that mitigation is the *first* line of defense that future
  feature has to reckon with, not something bolted on after the fact.

  This also makes the write/read split intentional rather than an inconsistency for a future
  reader to puzzle over: write's hard boundary can only ever *narrow* what `writeDirs.allow`
  grants (never widen past `workspace_root`), while read's table, once trusted, can both narrow
  and widen. That asymmetry is deliberate — read access is lower blast-radius than write access
  (leaking file contents into a model's context is bad; the model corrupting or exfiltrating
  data via an uncontrolled write is worse) — and is why only `ReadFile`, never the write tools,
  has a trust-gated escape hatch at all.

  `${workspace_root}/.klorb/` is implicitly write-denied regardless of any of the above (see
  `TODO.md`'s "Permissions" item) — the agent must not be able to use `EditFile`/`CreateFile` to
  rewrite its own `readDirs`/`writeDirs` grants, or a future persisted trust decision, and
  silently escalate its own access on a later reload. That check is unconditional, structural,
  and not part of the `writeDirs` table, so no `writeDirs.allow` entry (even one covering the
  whole workspace) can re-enable it. Unlocking it deliberately is left to a future
  `EscalatePrivileges` tool, not this pass.

  Known, deliberately out of scope: every permission check in this system resolves a path
  string at check time; nothing holds an open OS-level directory handle across the gap between
  that check and the caller's actual file I/O, so a directory rename or symlink swap in that
  window (TOCTOU) could redirect an approved operation to an unintended target. Closing that
  would require operating relative to an `os.open()`-obtained descriptor (e.g. with
  `O_NOFOLLOW`/`O_DIRECTORY`) rather than a re-resolved path, which is real, separate work — see
  `TODO.md`'s "Permissions" item.
