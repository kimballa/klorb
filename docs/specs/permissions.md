# Permissions

## Summary

`klorb.permissions` is a general-purpose framework for deciding whether a tool may access a
resource: three rule lists (`deny`, `ask`, `allow`), evaluated in that fixed category order, so
the strictest applicable rule always wins regardless of which config layer contributed it or
how specific it is. The first concrete resource kind is directory access
(`klorb.permissions.directory_access`), governing which directories the file tools
(`ReadFile`, `EditFile`, `ReplaceAll`, `CreateFile`) may read from and write to, via two new
`SessionConfig` fields, `read_dirs`/`write_dirs`, exposed on-disk as `readDirs`/`writeDirs`.
`TODO.md`'s "Permissions" backlog item anticipates further resource kinds (bash commands,
website access) built on the same abstraction. See
[the category-order ADR](../adrs/evaluate-permission-categories-deny-then-ask-then-allow.md),
[the read/trust ADR](../adrs/gate-read-hard-boundary-on-workspace-trust.md), and
[the write-verdict ADR](../adrs/write-verdict-is-stricter-of-read-and-write-tables.md) for the
reasoning behind the most consequential decisions here.

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
  time via `canonicalize_dir(path, workspace_root)`: a relative rule path is joined onto
  `workspace_root` first (so `Allow("..")` means the same thing as
  `Allow("<workspace_root>/..")`, not the process's current working directory), then the result
  is symlink- and `..`-resolved (`Path.resolve(strict=False)`, same algorithm
  `resolve_within_workspace` uses). It matches a candidate (which the caller must have already
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
  Falls back to `cwd` itself if nothing qualifies. `load_process_config()` calls this and
  threads the result into `SessionConfig.workspace_root` for every real run — a behavior change
  from the previous bare `Path.cwd()`: running klorb from a subdirectory of a project with
  `<project>/.klorb/` at its root now resolves `workspace_root` to `<project>`, not the
  subdirectory (matching `.git` ancestor search). `SessionConfig.workspace_root`'s own
  `Field(default_factory=Path.cwd)` is unchanged and remains the fallback for callers
  constructing `SessionConfig` directly (tests).

### Path resolution and enforcement (`klorb.permissions.workspace`)

(Formerly `klorb.tools._path_safety`; moved here because `SessionConfig` now holds a `DirRules`
field, so `klorb.session` must not import anything that imports `klorb.session` back —
`ToolSetupContext`, needed only as a type annotation in this module, is imported under
`TYPE_CHECKING` to avoid that cycle.)

* `canonicalize_candidate(context, filename) -> Path` — joins a relative `filename` onto
  `workspace_root`, resolves symlinks/`..`, no boundary check.
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
  This deliberately regresses the write tools' old zero-config default (`"allow"` everywhere in
  the workspace) to `"ask"` everywhere — an accepted tradeoff; see the ADR.
* `resolve_and_evaluate_read(context, filename) -> (Path, Verdict)` — evaluates `path` against
  `readDirs` alone; `writeDirs` is never consulted for a read (a write grant does not imply a
  read grant — see `evaluate_write()` for the converse relationship). Branches on
  `context.process_config.is_workspace_trusted`:
  * **Untrusted (default; the only state reachable by any production code path today)**:
    resolves via `resolve_within_workspace()` (same hard boundary as the write tools), then
    evaluates `readDirs` on that already-in-workspace path, falling back to `"allow"` if nothing
    matches (mirroring the write tools' old default, since the hard boundary already confines
    the candidate).
  * **Trusted (reserved for a future "trust this workspace" flow — not reachable today outside
    tests)**: resolves via `canonicalize_candidate()`, no boundary raise — so `readDirs.allow`
    can reach outside `workspace_root`. Falls back to `"deny"` if nothing matches: no implicit
    "inside the workspace" default here. Default grants in this mode are meant to come from an
    explicit `readDirs.allow` entry a future project-bootstrap flow writes into
    `.klorb/klorb-config.json` when a workspace is trusted (see `TODO.md`'s "project
    bootstrapping" item), not from a rule baked into this evaluator.

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
`is_workspace_trusted` has no on-disk key at all, anywhere — see "Known risks" below.

## Known risks

* **Untrusted-project self-granted read access, deferred until a future feature exists.**
  `.klorb/klorb-config.json` is the least-trusted config layer — it can arrive via whatever
  `cwd` an untrusted, cloned repository puts a user in — yet its `readDirs.allow` entries
  participate in the exact same flat, concatenated list as the user's own home config. Because
  `ReadFile` is gated by `is_workspace_trusted` (default `False`, not settable by any code path
  in this codebase outside tests), that risk cannot be reached today: every workspace klorb can
  actually run against gets the same hard boundary the write tools have. It becomes live again
  the moment a future "trust this workspace" flow (see `TODO.md`'s "project bootstrapping"
  item) sets `is_workspace_trusted = True` for a directory. Mitigation then, as now: a user- or
  `/etc`-level `readDirs.deny` for a sensitive path (`~/.ssh`, `~/.aws`, etc.) always outranks
  anything a project layer can add, since `deny` is evaluated first regardless of which layer
  contributed it. See [the read/trust ADR](../adrs/gate-read-hard-boundary-on-workspace-trust.md).
* **TOCTOU.** Every check here resolves a path string at check time; nothing pins an open
  OS-level directory handle across the gap between that check and the caller's actual file I/O,
  so a directory rename or symlink swap in that window could redirect an approved operation to
  an unintended target. Closing this would require operating relative to an `os.open()`-obtained
  descriptor (`O_NOFOLLOW`/`O_DIRECTORY`) rather than a re-resolved path string — real, separate
  work, tracked in `TODO.md`'s "Permissions" item, not implemented here.

## Out of scope

* Interactive "ask" confirmation: `PermissionAskRequired` fails closed for this pass (see
  TODO.md); no plumbing exists yet to actually pause and prompt the user mid-tool-call.
* Bash-command and website-access `PermissionsTable`s — future resource kinds noted in
  `TODO.md`, not built here.
* The "trust this workspace" bootstrap flow itself (interactively asking whether to treat a
  directory as a project, creating its `.klorb/klorb-config.json` with sensible default grants,
  and — eventually — setting `is_workspace_trusted`) is `TODO.md`'s "project bootstrapping"
  item; not built here. `find_workspace_root()`'s ancestor-search algorithm is written to match
  what that flow will need (step one of "project bootstrapping" is literally "attempts to
  identify the workspace root").
* `EscalatePrivileges` — the future tool that would grant a temporary, user-confirmed exception
  to the `privileged_dirs()` read/write deny — is `TODO.md`'s item, not built here.
