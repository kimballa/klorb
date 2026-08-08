# `EscalatePrivileges(homedir)` grants session-level readDirs/writeDirs *and* lifts the write-side workspace boundary

* Date: 2026-07-17 03:06
* Question: `EscalatePrivilegesTool`'s own docstring says the `"homedir"` scope "grants read/write
  access to `KLORB_DATA_DIR` and `KLORB_STATE_DIR`" (and `KLORB_CONFIG_DIR`). But the only thing
  approving that scope actually does today is add `"homedir"` to `SessionConfig.approved_scopes`,
  which `directory_access.privileged_dirs()` consults to *omit* those dirs from the
  self-tampering deny list. Omission from the privileged list is necessary but not sufficient for
  a real write:
  * A **read** in trusted mode goes through `resolve_and_evaluate_read()`, which skips the
    workspace-root boundary (`canonicalize_candidate`, not `resolve_within_workspace`) — but then
    falls back to `"ask"` when nothing in `readDirs` matches, and nothing grants `KLORB_DATA_DIR`.
  * A **write** goes through `resolve_and_evaluate_write()`, which — after `is_privileged_path()`
    and `write_files` — calls the hard `resolve_within_workspace()` boundary raise *before*
    `writeDirs` is ever consulted. `KLORB_DATA_DIR` is outside `workspace_root`, so that boundary
    rejects the path regardless of `approved_scopes`, which does not touch the boundary at all.

  So after `EscalatePrivileges(scope="homedir")` is approved, an ordinary
  `CreateFile`/`EditFile`/`ReadFile` against `$KLORB_DATA_DIR/...` still fails: reads fall through
  to `"ask"` with no matching rule, and writes are rejected by the workspace-root boundary before
  any table is consulted. The scope grants far less than its docstring promises. This surfaced
  while designing skill authoring (`docs/plans/drafting/010-skills.md`), where a user-tier skill
  is authored under `$KLORB_DATA_DIR/skills/` via the ordinary file tools after a `"homedir"`
  escalation — but it is a latent gap in `EscalatePrivileges(homedir)` itself, independent of
  skills.
* Answer: Approving `EscalatePrivileges(scope="homedir")` does three coordinated things, not one:
  1. Adds `"homedir"` to `SessionConfig.approved_scopes` (unchanged — lifts the
     `privileged_dirs()` self-tampering block for `KLORB_DATA_DIR`/`KLORB_CONFIG_DIR`/
     `KLORB_STATE_DIR`).
  2. Introduces **session-level** `readDirs.allow` *and* `writeDirs.allow` entries covering those
     same escalated directories — mutating only the live `SessionConfig.read_dirs`/`write_dirs`
     in memory (whole-object reassignment, per `DirRules`' immutable-by-convention contract), the
     same "this session" grant shape a persistent-scope permission decision uses. These are never
     written to disk and never rippled into the `ProcessConfig.session` template, so they revoke
     at session end exactly as `approved_scopes` does (a `/clear` starts a fresh `SessionConfig`
     with both an empty `approved_scopes` and no such entries).
  3. Lifts the write-side `resolve_within_workspace()` boundary for a candidate that falls within
     an approved escalation scope's directories — symmetric to how trusted-read mode already
     skips that boundary — so the `writeDirs.allow` entry from (2) can actually be reached and
     evaluated instead of the candidate being rejected for being outside `workspace_root`.

  The `"workspace"` scope needs none of this: `${workspace_root}/.klorb/` is already inside
  `workspace_root` (so no boundary lift is required), and a trusted workspace's init already
  writes `writeDirs.allow: [workspace_root]` (so once the privileged block is lifted, the write
  resolves to `allow` with no further grant). Only `"homedir"`, whose target dirs live outside any
  workspace root, needs the session-level grants and the boundary lift.
* Reasoning: The three effects are the minimum set that makes the scope mean what its docstring
  says. Dropping any one leaves the promise unfulfilled: without (1) the self-tampering deny fires;
  without (2) reads fall through to `"ask"` and writes have no `allow` to resolve to; without (3)
  the write boundary rejects the path before (2)'s grant is ever consulted.

  Expressing (2) as ordinary `readDirs`/`writeDirs.allow` entries — rather than a bespoke
  "escalated dirs are magically writable" branch threaded through every evaluator — keeps the
  grant in the same representation the rest of the permissions system already reasons about, so
  `evaluate_write()`'s stricter-of-read-and-write logic, the once/session override machinery, and
  every existing test-assertion shape all keep working unchanged. The boundary lift in (3) is the
  one thing that genuinely *can't* be expressed as a table entry, because the boundary raise is
  structural and runs ahead of the tables; scoping that lift to `approved_scopes`' own directories
  (not a blanket "escalation disables the boundary") keeps the hard workspace confinement fully in
  force for every path the user did not escalate to.

  Keeping the grants session-only and unpersisted matches `approved_scopes`' own contract
  (`SESSION_KEY_MAP` excludes it; no config file can pre-populate it) — an escalation is a
  deliberate, in-the-moment, single-session decision, and a persistent "always let this agent
  write my `KLORB_DATA_DIR`" grant would be a much larger, separate decision that this scope
  deliberately does not offer (the tool has no permanent-approval option). The alternative of
  persisting the grant was rejected for that reason: it would silently widen the agent's standing
  access to klorb's own process-wide state across future sessions from a single interstitial the
  user approved for one narrow, immediate purpose.
