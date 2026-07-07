# Permissions

## Summary

`klorb.permissions` is a general-purpose framework for deciding whether a tool may access a
resource: three rule lists (`deny`, `ask`, `allow`), evaluated in that fixed category order, so
the strictest applicable rule always wins regardless of which config layer contributed it or
how specific it is. The first concrete resource kind is directory access
(`klorb.permissions.directory_access`), governing which directories the file tools
(`ReadFile`, `EditFile`, `ReplaceAll`, `CreateFile`) may read from and write to, via two new
`SessionConfig` fields, `read_dirs`/`write_dirs`, exposed on-disk as `readDirs`/`writeDirs`.
`TODO.md`'s "Permissions" backlog item anticipated further resource kinds built on the same
abstraction; bash-command access (`CommandPermissionsTable`, gating `BashTool`) is the first of
those — see docs/specs/bash-tool-and-command-permissions.md — with website access still
outstanding. An `"ask"` verdict is no longer necessarily a
dead end either: the interactive TUI can route it through a modal (see "Interactive 'ask'
confirmation" below) that lets the user grant access once, for the session, or persistently at
the workspace or per-user level. See
[the category-order ADR](../adrs/evaluate-permission-categories-deny-then-ask-then-allow.md),
[the read/trust ADR](../adrs/gate-read-hard-boundary-on-workspace-trust.md),
[the write-verdict ADR](../adrs/write-verdict-is-stricter-of-read-and-write-tables.md),
[the ask-rule-promotion ADR](../adrs/promote-matched-ask-rule-path-not-candidate-on-grant.md),
[the cross-file-cleanup ADR](../adrs/homedir-grants-can-clean-workspace-ask-never-reverse.md),
[the once-grant ADR](../adrs/once-grants-bypass-via-tool-context-override-not-table.md),
[the grant-granularity ADR](../adrs/grant-unmentioned-paths-at-containing-directory.md), and
[the read/write-union ADR](../adrs/union-matched-ask-rules-across-read-and-write-tables.md) for
the reasoning behind the most consequential decisions here.

## How it works

### The abstraction (`klorb.permissions.table`)

* `Verdict` is `"deny" | "ask" | "allow"`.
* `PermissionsTable[T]` (`table.py`) is an abstract base holding three `list[T]` rule lists —
  `deny`, `ask`, `allow` — and an abstract `_matches(rule: T, candidate: T) -> bool` a concrete
  subclass implements per its own resource kind's matching semantics.
  `evaluate(candidate: T) -> Verdict | None` checks `deny`, then `ask`, then `allow`, in that
  fixed order, returning the category of the first list with any matching rule, or `None` if
  nothing in any list matches — callers are responsible for their own fallback in that case.
  Rule specificity never changes the category order: a broad `deny` always beats a narrower
  `allow` for the same candidate.
* `PermissionAskRequired` (a plain `Exception`, not a `PermissionError` subclass) is raised when
  a verdict is `"ask"` and there's no interactive plumbing to route the question through yet
  (see "Out of scope" below — this is the exception TODO.md already names: "Need to handle
  `PermissionAskRequired` exception with a user prompt").
* `raise_if_not_allowed(verdict, *, resource_description)` is the single seam that turns a
  verdict into an action: raises `PermissionError` for `"deny"`, `PermissionAskRequired` for
  `"ask"` (failing closed — treated as denial, just distinguishably), or returns normally for
  `"allow"`. Every tool call site enforces a verdict through this one function, so swapping
  `"ask"`'s fail-closed behavior for real interactive confirmation later is a one-function
  change, not a change at every call site.

### Directory access (`klorb.permissions.directory_access`)

* `DirRules` is a pydantic model: `deny`/`ask`/`allow`, each `list[Path]`, defaulting to empty.
  Treated as immutable after construction — nothing in this codebase mutates these lists in
  place, so `SessionConfig.model_copy()`'s shallow copy sharing the underlying list objects
  between a template and its cloned sessions is harmless.
* `DirectoryAccessTable(PermissionsTable[Path])` canonicalizes every rule path at construction
  time via `canonicalize_dir(path, workspace_root)`: a leading `~`/`~user` is expanded to the
  invoking user's home directory first (`Path.expanduser()`), then a still-relative rule path is
  joined onto `workspace_root` (so `Allow("..")` means the same thing as
  `Allow("<workspace_root>/..")`, not the process's current working directory), then the result
  is symlink- and `..`-resolved (`Path.resolve(strict=False)`, same algorithm
  `resolve_within_workspace` uses). `canonicalize_dir` is the single canonicalization primitive
  for the whole permissions system — `canonicalize_candidate` (below) calls it directly rather
  than duplicating the algorithm, so a model-supplied tool-call `filename` gets the same `~`
  expansion and workspace-relative join as a config-file rule path. It matches a candidate
  (which the caller must have already
  canonicalized the same way) via ancestor-or-equal containment:
  `candidate == rule or candidate.is_relative_to(rule)`. This is why
  `/home/the_project` allowed + `/home/the_project/private` denied correctly resolves
  `/home/the_project/foo.txt` to `allow` and `/home/the_project/private/nope.txt` to `deny` —
  `deny` is checked first as a category, and it's the more specific match.
* `privileged_dirs(workspace_root) -> list[Path]` returns the canonicalized list of every
  directory klorb's file tools may never read from or write to without a future
  `EscalatePrivileges` grant: `${workspace_root}/.klorb/` plus the process-wide
  `KLORB_CONFIG_DIR`/`KLORB_DATA_DIR`/`KLORB_STATE_DIR` from `klorb.paths` (resolved on each
  call, so an env-var override picked up by `klorb.paths` is honored, not baked in at import
  time). `is_privileged_path(path, workspace_root)` checks a single already-canonicalized
  candidate against that list using the same equal-or-descendant containment semantics as
  `DirectoryAccessTable`. Both live here (not in `klorb.permissions.workspace`) since they're
  pure directory-access data/logic with no `ToolSetupContext` dependency; `evaluate_write()` and
  `resolve_and_evaluate_read()` are the only callers, each treating a `True` result as an
  unconditional deny checked ahead of the `writeDirs`/`readDirs` tables — see "`.klorb`
  self-tampering protection" below.
* `find_workspace_root(cwd)` searches `cwd` and its ancestors for the nearest directory with an
  immediate-child `.klorb` that `is_dir()` and is not itself a symlink; a disqualified ancestor
  (symlinked or a plain file named `.klorb`) doesn't stop the search, it just keeps walking up.
  Falls back to `cwd` itself if nothing qualifies. `load_process_config()` calls this (via
  `klorb.workspace.TrustManager.resolve_workspace()`, when no `workspace` argument is given) and
  threads the result into `SessionConfig.workspace.path` for every real run: running klorb from
  a subdirectory of a project with `<project>/.klorb/` at its root resolves `workspace.path` to
  `<project>`, not the subdirectory (matching `.git` ancestor search). `SessionConfig.workspace`'s
  own `Field(default_factory=lambda: Workspace(path=Path.cwd()))` is the fallback for callers
  constructing `SessionConfig` directly (tests).

### Path resolution and enforcement (`klorb.permissions.workspace`)

(Formerly `klorb.tools._path_safety`; moved here because `SessionConfig` now holds a `DirRules`
field, so `klorb.session` must not import anything that imports `klorb.session` back —
`ToolSetupContext`, needed only as a type annotation in this module, is imported under
`TYPE_CHECKING` to avoid that cycle.)

* `canonicalize_candidate(context, filename) -> Path` — delegates to
  `directory_access.canonicalize_dir()`: expands a leading `~`/`~user`, joins a still-relative
  `filename` onto `workspace_root`, resolves symlinks/`..`, no boundary check. This means a
  model-supplied `filename` of `"~/.ssh/id_rsa"` resolves to the real home directory, not a
  literal `~` subdirectory under `workspace_root` — so it correctly falls outside the workspace
  boundary (`resolve_within_workspace()` raises) rather than silently resolving to a
  nonexistent in-workspace path.
* `resolve_within_workspace(context, filename) -> Path` — canonicalizes via the above, then
  raises `PermissionError` if the result isn't `workspace_root` or beneath it. Unchanged
  behavior from before this feature; still used by the three write tools as their first,
  hard, non-config-overridable check.
* `evaluate_write(context, path) -> Verdict` — called on a path that has already passed
  `resolve_within_workspace()`. Checks `directory_access.is_privileged_path()` first (not part
  of either table — no `allow` entry, even one covering the whole workspace, can re-enable it;
  see "`.klorb` self-tampering protection" below), then evaluates `path` against *both*
  `readDirs` and `writeDirs` independently and returns the **stricter** of the two, so write can
  never be more permissive than read for the same path — see
  [the write-verdict ADR](../adrs/write-verdict-is-stricter-of-read-and-write-tables.md). Each
  table's raw result (`"deny"`/`"ask"`/`"allow"`, or `None` if none of its three lists match at
  all) is normalized via `_normalize_for_write()` before comparing: `None` becomes `"ask"`, not
  a permissive default, so write is `"allow"` only when *both* tables explicitly say `allow`.
  With both tables empty, this normalizes to `"ask"` everywhere in the workspace — see the ADR
  for the reasoning behind that default.
* `resolve_and_evaluate_read(context, filename) -> (Path, Verdict)` — evaluates `path` against
  `readDirs` alone; `writeDirs` is never consulted for a read (a write grant does not imply a
  read grant — see `evaluate_write()` for the converse relationship). Branches on
  `context.session_config.workspace.trusted`:
  * **Untrusted (default)**: resolves via `resolve_within_workspace()` (same hard boundary as
    the write tools), then evaluates `readDirs` on that already-in-workspace path, falling back
    to `"allow"` if nothing matches — a permissive default that's safe here because the hard
    boundary already confines the candidate, and because this fallback plays no part in
    `evaluate_write()`'s stricter-of-read-and-write computation (`writeDirs` still requires its
    own explicit grant).
  * **Trusted (set via the interactive workspace-trust flow — see [[projects-and-trust]])**:
    resolves via `canonicalize_candidate()`, no boundary raise — so `readDirs.allow` can reach
    outside `workspace_root`. Falls back to `"deny"` if nothing matches: no implicit "inside the
    workspace" default here. Default grants in this mode come from an explicit `readDirs.allow`
    entry `klorb.workspace.workspace_init.write_initial_project_config()` writes into
    `.klorb/klorb-config.json` when a workspace is opened as a trusted project, not from a rule
    baked into this evaluator.

  In both modes, `directory_access.is_privileged_path()` is then checked, unconditionally,
  before the table — the read-side counterpart of `evaluate_write()`'s hard deny, so no
  `readDirs.allow` entry (trusted mode) or table fallback (untrusted mode) can grant access to
  `${workspace_root}/.klorb/` or the process-wide `KLORB_CONFIG_DIR`/`KLORB_DATA_DIR`/
  `KLORB_STATE_DIR`.

### Tool integration

* `EditFile`/`ReplaceAll`/`CreateFile`: `resolve_within_workspace()` (unchanged) →
  `raise_if_not_allowed(evaluate_write(...))` → disk I/O.
* `ReadFile`: `resolve_and_evaluate_read()` → `raise_if_not_allowed(verdict)` → `open()`. This
  is `ReadFile`'s first-ever confinement — previously it called `open(filename)` directly, with
  no resolution or boundary check at all.
* `ListDir`: `resolve_and_evaluate_read()` on `dirname` → `raise_if_not_allowed(verdict)` →
  `iterdir()`, same single-path shape as `ReadFile`.
* `Grep`/`FindFile`: `klorb.tools.dir_walk.walk_readable_tree()` applies the same
  `resolve_and_evaluate_read()` check to `dirname` itself (raising exactly like `ListDir` if it
  isn't `"allow"`), then re-applies it to every subdirectory the recursive walk considers
  descending into — one that isn't `"allow"` is excluded from the walk rather than raising, so a
  single restricted subtree doesn't abort an otherwise-permitted bulk search. See
  docs/specs/tool-framework.md's "Recursive tree walks" section and
  [the pruning ADR](../adrs/prune-non-allow-subdirs-during-recursive-tree-walk.md) for the full
  reasoning.

### Internal privileged paths and `.klorb` self-tampering protection

`${workspace_root}/.klorb/` holds `klorb-config.json` (and, per `TODO.md`, future session
state); `klorb.paths.KLORB_CONFIG_DIR`/`KLORB_DATA_DIR`/`KLORB_STATE_DIR` hold klorb's
process-wide config, data, and state outside any one workspace (session logs, and whatever else
lands there per `paths.py`). Both reads and writes to any of these are implicitly denied,
structurally, regardless of `readDirs`/`writeDirs` config — so the agent can't use
`ReadFile`/`EditFile`/`CreateFile` to inspect or rewrite its own permission grants (or other
klorb-internal state) and silently escalate its own access on a later config reload.
`directory_access.privileged_dirs(workspace_root)` is the single canonicalized list both
`evaluate_write()` and `resolve_and_evaluate_read()` check via `is_privileged_path()`; adding a
new internal directory to this protection means adding it there once, not touching each call
site. `TODO.md` notes the intended unlock path is a future `EscalatePrivileges` tool that grants
a temporary, user-confirmed exception through the end of a turn; that tool doesn't exist yet, so
today the deny is absolute.

## Configuration

`readDirs`/`writeDirs`, each `{"deny": [...], "ask": [...], "allow": [...]}` (lists of path
strings), nest under `sessionDefaults` — see `docs/specs/process-and-session-config.md`'s
"On-disk key naming" for the full file shape:

```json
{
  "sessionDefaults": {
    "readDirs": {"deny": ["/nope"], "ask": ["/home/aaron/maybe"], "allow": ["/yolo", "/goforit"]},
    "writeDirs": {"deny": [], "ask": [], "allow": []}
  }
}
```

Note that with `writeDirs` empty as above, `/yolo` and `/goforit` are readable but not writable:
`evaluate_write()` requires an explicit `allow` in *both* tables (see
[the write-verdict ADR](../adrs/write-verdict-is-stricter-of-read-and-write-tables.md)), so a
broad `readDirs.allow` with no corresponding `writeDirs.allow` entry never implies write access.

Unlike every other `sessionDefaults`/top-level key, `readDirs`/`writeDirs` are merged by
**concatenating** each category's array across all five config layers (not replaced) — see
[the category-order ADR](../adrs/evaluate-permission-categories-deny-then-ask-then-allow.md).
`SessionConfig.workspace` has no on-disk key at all, anywhere — see "Known risks" below.

## Known risks

* **Trusted-project `readDirs.allow` participates in the same flat, concatenated list as the
  user's own home config.** Once a workspace is trusted (see [[projects-and-trust]]),
  `.klorb/klorb-config.json` — still the least-trusted config layer, since it arrived via
  whatever directory the user chose to trust — is read, and its `readDirs.allow` entries
  concatenate into the same list `readDirs.deny` from every other layer is checked against.
  Mitigation: a user- or `/etc`-level `readDirs.deny` for a sensitive path (`~/.ssh`, `~/.aws`,
  etc.) always outranks anything a project layer can add, since `deny` is evaluated first
  regardless of which layer contributed it — and, per [[projects-and-trust]], a workspace only
  reaches this state via an explicit interactive trust decision (or a prior session's recorded
  one), never a config file granting itself trust. See
  [the read/trust ADR](../adrs/gate-read-hard-boundary-on-workspace-trust.md).
* **TOCTOU.** Every check here resolves a path string at check time; nothing pins an open
  OS-level directory handle across the gap between that check and the caller's actual file I/O,
  so a directory rename or symlink swap in that window could redirect an approved operation to
  an unintended target. Closing this would require operating relative to an `os.open()`-obtained
  descriptor (`O_NOFOLLOW`/`O_DIRECTORY`) rather than a re-resolved path string — real, separate
  work, tracked in `TODO.md`'s "Permissions" item, not implemented here.

## Interactive "ask" confirmation

When a tool call raises `PermissionAskRequired`, how it's resolved is governed by
`SessionConfig.permission_framework` (`klorb.session.PermissionFramework`, `"ask" | "auto" |
"deny"`), checked first in `Session._run_tool_calls()` before it ever looks at any callback:

* **`"deny"`** — every ask fails closed, exactly like the plain fail-closed case described
  above, without invoking any callback.
* **`"auto"`** — every ask is auto-approved via a synthesized
  `PermissionDecision(choice="session")`, applied through the same
  `Session._retry_after_permission_decision()`/`apply_permission_grant()` path a real
  "Allow (this session)" answer would take (see below) — an in-memory-only grant for the
  rest of this session, nothing persisted to disk. No callback is invoked either.
* **`"ask"`** (the `SessionConfig` default) — falls through to the optional callback
  mechanism: when the interactive TUI is running, a modal
  (`klorb.tui.permission_ask_screen.PermissionAskScreen`) asks the user how to proceed,
  instead of failing closed. This is wired in as an optional callback, not a change to the
  fail-closed default: `Session.send_turn()`/`retry_last_turn()` take an `on_permission_ask`
  parameter (`klorb.session.PermissionAskContext -> klorb.session.PermissionDecision`),
  threaded through to `Session._run_tool_calls()`. With no callback given, behavior is
  unchanged from the plain fail-closed case described above. `ReplApp._on_permission_ask`
  (`klorb.tui.repl`) is the TUI's implementation, mirroring the existing
  `on_tool_call_limit_reached`/`ToolCallLimitScreen` pattern: it blocks the worker thread
  running `Session.send_turn()` via `call_from_thread`, shows the modal on the app's own
  event loop, and returns once the user answers.

`permission_framework` defaults to `"ask"` when the session is interactive and `"deny"`
otherwise — resolved by `klorb.cli.main()`, not config-file-driven, since it depends on the
CLI-resolved `interactive` value (see docs/specs/session-and-turns.md and
[[default-permission-framework-to-deny-headlessly]]). `-y`/`--auto-approve` forces `"auto"`
regardless of interactivity, for both a one-shot prompt and the REPL. The REPL's status row
shows the session's current `permission_framework` value as a small badge next to the token
tally (see [[terminal-repl]]).

### The six choices

`PermissionAskScreen` offers exactly the six choices from `TODO.md`'s "Permissions" item:

* **Allow (once)** — bypasses the check for this one tool call only, persisting nothing at all,
  not even in memory. Implemented via a new `ToolSetupContext.permission_override: Path | None`
  field: `evaluate_write()`/`resolve_and_evaluate_read()` (`klorb.permissions.workspace`) check
  it — after the unconditional `is_privileged_path()` deny, before consulting either table — and
  short-circuit to `"allow"` on an exact match. `Session._retry_after_permission_decision()`
  retries the call via `tool_registry.instantiate_tool(name, permission_override=ask_exc.path)`,
  a fresh, one-shot `ToolSetupContext` discarded after that single retry — the identical access
  asks again next time, since no `readDirs`/`writeDirs` entry was ever added.
* **Allow (this session)** — mutates only the live `Session.config.read_dirs`/`write_dirs`
  (whole-object reassignment, never `.append()`ed in place — see `DirRules`'s own
  immutable-by-convention contract). Nothing is written to disk, and the process-config
  template (`ProcessConfig.session`) is untouched, so a future `/clear` does not inherit it.
* **Allow (always, in this workspace)** — additionally mutates `ProcessConfig.session.read_dirs`/
  `write_dirs` (so a `/clear`'d session in this same process inherits it too) and persists the
  grant to `${workspace_root}/.klorb/klorb-config.json`, auto-creating the file (and the
  `.klorb/` directory) if neither exists yet. This does **not** set `workspace.trusted` —
  granting r/w access to one directory is a much narrower action than trusting the whole
  workspace (see the read/trust ADR above); `ReadFile`'s hard workspace-root boundary is
  unaffected.
* **Allow (always, for me)** — the same in-memory dual-write as "this workspace" (live session
  config *and* the process-config template), but persists to the per-user file
  (`~/.config/klorb/klorb-config.json` by default — `klorb.process_config.user_config_path()`)
  instead of the project one, auto-creating it the same way. See "Cross-file cleanup" below for
  the one thing this scope does to the *workspace* file.
* **Deny** — declines this one call, persisting nothing, ever (there is no "Deny (this
  session)"/"(always)" variant — only Allow has graduated scopes).
* **Other** — functionally identical to Deny (denies this one call, persists nothing), but the
  free text the user typed is appended to the tool call's `tool_response` content alongside the
  generic denial, so the model sees the redirection (e.g. "use `/tmp/scratch` instead") without
  needing a second round trip.

All of this is implemented in `klorb.permissions.grant`
(`compute_grant_paths()`/`apply_permission_grant()`) and invoked by `Session` itself —
`Session._retry_after_permission_decision` calls `apply_permission_grant()`, passing its own
`config` (the live `SessionConfig`) and the `ProcessConfig` reference it was constructed with
(see "Configuration" below), once `on_permission_ask` returns a persistent-scope decision, before
retrying the call. `ReplApp._on_permission_ask` only shows the modal (`compute_grant_paths()` is
also called there, read-only, purely so the modal's copy can name the directory a grant would
cover) and returns the user's choice — it never applies or persists a grant itself. This keeps
the grant-computation and file-persistence logic entirely inside the library layer, importable
and unit-testable without Textual, and automatically available to any other consumer of
`Session` (a future VSCode plugin, say) without reimplementing this dance — per this repo's
CLI/library firewall (see `CLAUDE.md`). A `Session` constructed with no `ProcessConfig` at all
(`process_config=None`) still gets a working grant — `apply_permission_grant()` just skips the
process-wide ripple described below, since there's no in-memory `ProcessConfig.session` template
to ripple into; the live `SessionConfig` mutation and the on-disk persistence, which only need
`SessionConfig.workspace.path`, still happen.

### Grant granularity: directory, not file

An Allow grant is never recorded at the exact file the model happened to touch. Two cases:

* **A matching `ask` rule already exists** (`readDirs.ask`/`writeDirs.ask` — checked via the new
  `PermissionsTable.matching_rules(category, candidate)`, which reports *which* rule(s) matched,
  not just the winning category): the grant is recorded at *that rule's own path*, promoting it
  from `ask` to `allow` (and removing it from `ask`), preserving whatever breadth the original
  rule had. If more than one `ask` rule matches (in `readDirs`, or — for a write access — unioned
  with `writeDirs` too, see below), all of them are promoted.
* **Nothing matched at all** ("the dir was never mentioned" — e.g. a bare write interrogation's
  `None`-normalized-to-`"ask"` default): the grant is recorded at the *containing directory* of
  the accessed path (`path.parent`), not the single file. `PermissionAskScreen`'s copy states
  this explicitly before the user picks a scope, since the user is granting more than what they
  see the model touching right now.

Granting the narrower, single accessed file instead of its directory (in the no-match case)
would mean a subsequent access to any *other* file in the same directory triggers a fresh ask —
for `writeDirs`, forever, one file at a time, since write's own no-match fallback is `"ask"`, not
a permissive default. Recording the grant at the directory avoids that.

### Write grants an equivalent read grant; read never implies write

A write access (`is_write=True`) always grants the *same* computed path(s) to `readDirs.allow`
too, alongside `writeDirs.allow` — `evaluate_write()` requires an explicit `allow` in *both*
tables (see the write-verdict ADR above), so granting write without read would immediately
re-cap the just-granted access back down to `ask` on the very next call. `compute_grant_paths()`
implements this by unioning matched `ask` rules across *both* `readDirs` and `writeDirs` for a
write access, and using that single unioned set for both tables' promotion — independently
removing each table's own matching `ask` entries, never cross-contaminating the two lists. A
read-only access (`is_write=False`) never touches `writeDirs` at all, in either direction —
consulting only `readDirs.ask`, granting only `readDirs.allow` — matching
`resolve_and_evaluate_read()`'s own read-only semantics.

### Cross-file cleanup: homedir may clean workspace, never the reverse

Persisting a grant means reading the *one target file's own* raw JSON
(`sessionDefaults.readDirs`/`writeDirs`) and mutating only that file's own `allow`/`ask` arrays,
then writing it back with every other key untouched — never reconstructing the file from the
in-memory, all-layers-concatenated `SessionConfig.read_dirs`/`write_dirs`, which would leak
entries that don't belong to that file's own scope.

Because config layers are concatenated with no rule-provenance tracking (see "Configuration"
below), "does this file contain the matched `ask` rule" is re-derived on demand by reading each
candidate file directly, rather than tracked through the merge. This makes the following
asymmetry straightforward to implement: a **homedir**-scope grant may *additionally* clean a
matching, now-redundant `ask` entry out of the **workspace** file if one independently exists
there — removal only, never an addition to that file's `allow` — since the user's homedir-scope
decision is the more-trusted, broader action superseding a less-trusted project file's `ask`
entry. The reverse never happens: a **workspace**-scope grant never opens the homedir (or
`/etc`) file at all, for either reading or writing — a workspace file is the least-trusted
config layer (it can arrive via a hostile cloned repository's `cwd`) and must never be able to
mutate a more-trusted, personal or admin-controlled file.

In every case, in-memory removal from the live `SessionConfig` happens unconditionally,
regardless of which file(s) actually get touched — so the *running process* is never left
dominated by a stale `ask` entry it just granted an offsetting `allow` for, even when a
lower-trust scope couldn't clean up a higher-trust file. The `ProcessConfig.session` template is
updated the same way for `"workspace"`/`"homedir"` scope too, *when a `ProcessConfig` is
available* — a `Session` given none at construction has no template to update, so that half of
the mutation is simply skipped (see above). A stale `ask` entry surviving in a file this feature
never reaches (a `/etc`-level entry, or a homedir entry left behind by a workspace-scope grant)
will still apply on the *next* process start, since category-order evaluation over concatenated
layers means an `ask` entry from any layer keeps beating an `allow` entry from any other layer —
this is an inherent, already-documented property of the category-order/concatenation design
(see the category-order ADR above), not something this feature can or should work around; the
only way to actually remove such an entry is to edit or delete the file that contributed it.

### Trusted harness code, not a bypass

`klorb.permissions.grant`'s file writes go straight through `pathlib`/`json` (via
`klorb.schema_envelope.write_versioned_json()`), not through `CreateFileTool`/`EditFileTool` or
any `PermissionsTable` check — including for `${workspace_root}/.klorb/klorb-config.json`, which
is unconditionally privileged against the *agent's own tools* (see "`.klorb` self-tampering
protection" above). This is intentional, not a bypass bug: it's trusted harness code, not the
agent, writing the file, in direct response to an explicit interactive user decision — exactly
like a human hand-editing `klorb-config.json` themselves. The `.klorb` self-tampering
protection's job is to stop the *agent* from rewriting its own grants via `ReadFile`/`EditFile`;
it has nothing to say about the harness's own trusted config-persistence code.

## Out of scope

* Website-access `PermissionsTable` — a future resource kind noted in `TODO.md`, not built here.
  Bash-command access (`CommandPermissionsTable`) is a second concrete resource kind built on the
  same `PermissionsTable` abstraction described here — see
  docs/specs/bash-tool-and-command-permissions.md.
* The interactive workspace-trust bootstrap flow (asking whether to treat a directory as a
  project, creating its `.klorb/klorb-config.json`, and setting `workspace.trusted`) lives in
  [[projects-and-trust]], not here — this spec only covers how `workspace.trusted` and
  `readDirs`/`writeDirs` are *evaluated* once resolved, not how a `Workspace` gets resolved in
  the first place.
* `EscalatePrivileges` — the future tool that would grant a temporary, user-confirmed exception
  to the `privileged_dirs()` read/write deny — is `TODO.md`'s item, not built here.
