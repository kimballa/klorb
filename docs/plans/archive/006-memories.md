
# Memories

A way for the agent to make permanent notes to itself about things in the current workspace/project,
or the broader user / homedir environment.

## Tools

This introduces the following tools

* ListMemories - enumerate the various memories available.
* SearchMemories - find memories that recall various keywords
* ReadMemory - read a memory file
* EditMemory - update a memory file
* CreateMemory - create a new memory file.
* DeleteMemory - remove a memory file.

## Memories are files

Memories are stored in markdown files in well-defined directories:

* `global` memories are stored in `$HOME/.local/share/klorb/memories/`
* `workspace` memories are stored in `${workspaceRoot}/.klorb/memories/`

The agent does not think of these as specific paths, though. The agent thinks
about two *flat* namespaces of files.

* The tools each take a `namespace` argument of either `global` or `workspace`
* The tools take a `filename` argument for a `name-of-a-memory.md` file within
  the specified namespace.
  * The path separator character is forbidden, as are any attempts to escape the
    relevant `..../memories/` dir through `..`, etc.

The Create, Read, and Edit tools operate through the same common basis as
CreateFile, ReadFile, EditFile already do with the shared componentry for these
tools already shared with e.g. ReadScratchpad and EditScratchpad.

The 'create' file machinery has not been abstracted away from CreateFile yet,
in the same way that ReadFileCore and EditFileCore have been abstracted out
from ReadFile and EditFile, but that is a task needed as part of this plan.

### File format

* In general memories are considered ordinary markdown files and the agent is free
  to record in them as it sees fit.
* The first line of the file is the *topic* of the memory file.
* The first line must not be empty. A `CreateFile` or `EditFile` tool call
  that would cause the first line to be blank must fail, and provide an error
  back to the agent that informs it about this restriction.

## Listing memories

The ListMemories tool returns an object of the following format:

```json
{
    "global": [
        {
            "filename": "memory-filename-within-global-namespace.md",
            "topic": "First line of the file."
        },
        ... /* List all such files in the namespace. */
    ],
    "workspace": [
        /* Same format as `global` above. */
    ]
}
```

* If a memory file is empty, the `topic` should be the empty string `""`.
* If there are no global (or workspace) memories, then they are reported like `"global": []`
  or `"workspace": []`.

## Searching memories

The search process should operate similar to other Grep and SearchScratchpad
tools, accepting a list of `queries`. It should return structured json objects identifying all the hits,
with both `namespace` and `filename` attributes so the agent knows where they
belong. The `filename` attr should just be the filename *within* the namespace dir, not the
complete path to the file. We **never** report the full `/path/to/memories/filename.md`.

The `filename` should also be the subject of the search. That is, the line-by-line `re.match` search
algorithm used e.g. by SearchScratchpad should also apply `re.match` to the filename itself, as this
is semantically meaningful.  In the case when the filename is the match (e.g. "you-like-birds.md"
should match the search string `bird`), report the first non-blank line of the file as the matching
line, regardless of whether the contents of that line actually matches the query. This search tool
therefore also has a bit of "find file"-like behavior to it.

## Tool permissions

Default permission for operations are controlled by the following config flags,
at the process level:

* `tools.memory.readPermission` - also governs ability to list and search memories.
* `tools.memory.editPermission`
* `tools.memory.createPermission`
* `tools.memory.deletePermission`

The value for each is one of `deny`, `ask`, `allow`.

System defaults:

* read: allow
* edit: allow
* create: ask
* delete: ask

### Workspace memories are only accessible in trusted workspaces

The `.klorb/memories/` dir of **untrusted** workspaces are not accessible to the tools.

* ListMemories returns an empty list for the `workspace` namespace.
* SearchMemories does not iterate over any of the files for searching.
* ReadMemory, CreateMemory, and EditMemory for a workspace memory are all instantly
  permission-denied without appeal to the user.

## System prompt

The primary system prompt is altered to have a section about memories, explaining briefly
what its purpose is (to remember important details of how to work within the workspace, or
user preferences or directives that span across all workspaces, in a way that is durable
across sessions).

## Specific test cases to implement

### `Memories` common (`test_memories_common.py`, mirroring `test_scratchpad_common.py`)

* `global` namespace resolves to `KLORB_DATA_DIR / "memories"` (honoring a `KLORB_DATA_DIR`
  env override, per `klorb.paths`); `workspace` namespace resolves to
  `${workspace_root}/.klorb/memories/`.
* Both namespace directories are created on demand (first write), not eagerly at `Session`
  construction — unlike `Scratchpad`, there is no single file to `touch()` into existence.
* `filename` validation: rejects a path separator (`/` or `os.sep`) anywhere in `filename`,
  and rejects any `filename` that resolves (via `..`) outside its namespace directory. Cover
  `../etc/passwd`, `foo/../../bar.md`, an absolute path, and a leading `~`.
* `filename` not ending in `.md` — decide and test one behavior consistently (either reject,
  or silently normalize by appending `.md`); the plan doc doesn't currently say which.
* Two memory files with the same name in different namespaces (`global` vs `workspace`) are
  independent — writing/deleting one never touches the other.

### `ListMemoriesTool` (`test_list_memories.py`)

* Empty `global` and empty `workspace` each report `[]`, not an absent key.
* Reports every `.md` file in each namespace with `filename` and `topic` (first line).
* A zero-byte memory file reports `topic == ""`.
* A memory file whose first line is only whitespace also reports `topic == ""` (not the
  whitespace itself) — confirm the exact trim semantics against the "first line" spec text.
* Non-`.md` files (or files starting with `.`) present in a memories directory are excluded
  from the listing.
* Untrusted workspace: `workspace` key is always `[]`, even when `.klorb/memories/` exists on
  disk with files in it — `global` is unaffected by workspace trust.
* Listing does not recurse into subdirectories of a memories dir (flat namespace only).

### `SearchMemoriesTool` (`test_search_memories.py`, mirroring `test_search_scratchpad.py`)

* Multiple `queries` matched as a case-insensitive literal-substring OR (not regex) — same
  `re.escape`-and-alternate construction as `SearchScratchpadTool`.
* A hit reports both `namespace` and `filename`, and `filename` is the bare name (no
  directory component, no full path) — assert the full `/path/to/memories/...` string never
  appears anywhere in a result.
* Filename-as-match behavior: `queries=["bird"]` matches a file named `you-like-birds.md`
  even if no line inside it contains "bird" — and the reported matching line is the file's
  first non-blank line, not a fabricated "filename" pseudo-line, regardless of whether that
  line itself matches.
* A file matched only by filename (no line match) and a file matched by both filename and a
  line's content are each reported correctly without duplicate/aliased entries for the same
  file.
* Searches across both namespaces at once (or confirm/implement a `namespace` filter
  argument if the plan intends one — the plan doc doesn't say, so pin down the parameter
  shape before writing this tool).
* Untrusted workspace: search yields zero `workspace`-namespace results even for a query that
  would otherwise match a file physically present in `.klorb/memories/`.
* Empty namespace (no files at all) returns no hits, not an error.

### `ReadMemoryTool` (`test_read_memory.py`, mirroring `test_read_scratchpad.py`)

* Delegates line-range paging to `ReadFileCore` identically to `ReadScratchpadTool`: same
  `start_line`/`end_line`/`truncated`/`total_lines`/`"N|"`-prefixed `content` contract —
  reuse (don't reimplement) `test_read_scratchpad.py`'s cases against a memory file instead
  of the scratchpad.
* Reading a nonexistent `filename` raises a clear, tool-appropriate error (not a raw
  `FileNotFoundError` traceback).
* Reading a `workspace`-namespace memory in an untrusted workspace is permission-denied
  without ever reaching `ReadFileCore` (i.e. it doesn't leak line content in the error).
* Reading a `global`-namespace memory is unaffected by workspace trust.
* `tools.memory.readPermission` config: `deny` raises `PermissionError`; `ask` raises
  `PermissionAskRequired`; `allow` (the default) succeeds — for both namespaces.

### `EditMemoryTool` (`test_edit_memory.py`, mirroring `test_edit_scratchpad.py`)

* Delegates to `EditFileCore` identically to `EditScratchpadTool` — reuse
  `test_edit_scratchpad.py`'s drift-tolerance/"Ambiguous match"/empty-file/insert/delete
  cases against a memory file.
* First-line-must-not-be-empty invariant: an edit that would leave line 1 blank (replacing
  it with an empty string, or deleting it entirely so a later line becomes line 1 and *it*
  starts blank) fails with an error naming the restriction — cover both "edit targets line 1
  directly" and "edit deletes line 1, promoting a blank line 2."
  * Also cover the file-currently-empty edge case: what creates the first line of a
    brand-new memory (via `EditMemory` on a file `CreateMemory` made with empty content, or
    however creation is expected to seed content) — the plan implies `CreateMemory` produces
    the file and `EditMemory` fills it in, but doesn't spell out whether `CreateMemory`
    itself must reject empty content up front, given the first-line rule. Decide and test.
* `tools.memory.editPermission` config: `deny`/`ask`/`allow` behaviors, both namespaces.
* Editing a `workspace`-namespace memory in an untrusted workspace is permission-denied.
* No `readDirs`/`writeDirs` table is consulted at all (same bypass rationale as
  `docs/adrs/scratchpad-tools-bypass-permission-tables.md`) — assert a `writeDirs.deny`
  covering the memories directory has no effect on `EditMemory`.

### `CreateMemoryTool` (`test_create_memory.py`, mirroring `test_create_file.py`)

* Creates a new file in the given namespace with the given content; fails
  (`FileExistsError`-equivalent) if a memory with that `filename` already exists in that
  namespace — same "never implicitly overwrite" contract as `CreateFileTool`.
* Auto-creates the namespace directory (and any configured parent, e.g. `.klorb/`) if it
  doesn't exist yet, mirroring `CreateFileTool`'s `path.parent.mkdir(parents=True,
  exist_ok=True)`.
* First-line-not-blank validation applies at creation time too (content whose first line is
  empty is rejected), consistent with the rule as stated for `EditMemory`.
* `tools.memory.createPermission` config: `deny`/`ask`/`allow` (default `ask`) behaviors,
  both namespaces.
* Creating a `workspace`-namespace memory in an untrusted workspace is permission-denied.
* `filename` traversal/separator rejection (same cases as the common-module tests above),
  asserted again at the tool layer to confirm the tool actually calls the shared validator.

### `DeleteMemoryTool` (`test_delete_memory.py`, new pattern — no existing `DeleteFile` tool

to mirror)

* Deletes an existing memory file; raises a clear error for a nonexistent `filename` rather
  than a raw `FileNotFoundError`.
* `tools.memory.deletePermission` config: `deny`/`ask`/`allow` (default `ask`) behaviors,
  both namespaces.
* Deleting a `workspace`-namespace memory in an untrusted workspace is permission-denied
  *before* the file is touched — assert the file still exists on disk after a denied attempt.
* Deleting does not remove the namespace directory itself, even if it empties it.
* `filename` traversal/separator rejection, same as `CreateMemory`.

### `CreateFileCore` extraction (`test_create_file_core.py`, new — parallels

`ReadFileCore`/`EditFileCore`)

* Once `CreateFileTool`'s file-creation mechanic is pulled out into
  `klorb.tools.util.CreateFileCore` (see TODO items below), port `test_create_file.py`'s
  non-permission-related cases (`test_creates_a_new_file`, `test_creates_an_empty_file`,
  `test_raises_if_file_already_exists`, `test_creates_missing_parent_directories`) to run
  against the core directly, the same way `ReadFileCore`/`EditFileCore` are tested
  independently of `ReadFileTool`/`EditFileTool`.
* `CreateMemoryTool` and `CreateFileTool` each get a focused test verifying they delegate to
  `self.create_file_core.apply(...)` for the file-creation mechanic and only add their own
  path-resolution/permission-check logic around it — mirroring the existing
  `read_file_core`/`edit_file_core` delegation tests for the Read/Edit pairs.

### System prompt (`test_system_prompt.py` or wherever `default_sys.md` content is

asserted, if anywhere)

* If there's an existing test asserting specific sections appear in
  `default_sys.md` (check how the "Use your scratchpad" section is covered, if at all), add
  the equivalent assertion for the new "Memories" section.

### Permission-flag plumbing (`test_process_config.py`)

* `tools.memory.readPermission`/`editPermission`/`createPermission`/`deletePermission`
  round-trip through `klorb-config.json` the same way `tools.readFile.maxLines` etc. do
  (see `_CONFIG_KEY_MAP` in `klorb/src/klorb/process_config.py`), each defaulting per the
  spec (`read`/`edit` → `allow`, `create`/`delete` → `ask`) when omitted.
* An invalid value (anything other than `"deny"`/`"ask"`/`"allow"`) fails config load with a
  clear error, consistent with how other enum-shaped config keys are validated elsewhere.

## Specific TODO Items for Tasks to Implement Memories

1. **Extract `CreateFileCore`** into `klorb/src/klorb/tools/util/create_file_core.py`,
   re-exported from `klorb/src/klorb/tools/util/__init__.py` alongside `ReadFileCore`/
   `EditFileCore` (see that package's docstring and
   `docs/adrs/read-edit-file-scratchpad-share-core-via-composition.md` for the established
   shape: a plain class over a `pathlib.Path`, no `ToolSetupContext` dependency, holding the
   "does this file already exist / write content / build the result dict" mechanic). Update
   `CreateFileTool` (`klorb/src/klorb/tools/create_file.py`) to hold
   `self.create_file_core: CreateFileCore` and delegate to it, exactly like `ReadFileTool`/
   `EditFileTool` already do for their own cores. This must land before `CreateMemoryTool`
   below, since it's the shared mechanic the new tool composes rather than reimplements.

2. **Add `klorb/src/klorb/tools/memory/` subpackage** (mirroring
   `klorb/src/klorb/tools/scratchpad/`'s layout):
   * `common.py`: a `Memories` (or similarly named) helper owning namespace-to-directory
     resolution (`global` → `KLORB_DATA_DIR / "memories"`, `workspace` →
     `${workspace_root}/.klorb/memories/`, both created on demand rather than eagerly), the
     shared `filename` validator (rejects path separators and `..`-escape, per
     `canonicalize_dir`'s existing escape-rejection precedent in
     `klorb.permissions.directory_access` — reuse that primitive rather than
     hand-rolling a second path-traversal check), and the "workspace namespace is
     inaccessible when untrusted" gate consulted by every tool below.
   * An empty `__init__.py` importing none of the `Tool` subclasses, matching
     `klorb.tools.scratchpad`'s own docstring rationale (avoid the import cycle that
     `ToolRegistry._discover_tools()`'s recursive `pkgutil.walk_packages` walk exists to
     make unnecessary).
   * One module per tool: `list_memories.py`, `search_memories.py`, `read_memory.py`,
     `edit_memory.py`, `create_memory.py`, `delete_memory.py`.

3. **Implement `ListMemoriesTool`**: no arguments; returns the
   `{"global": [...], "workspace": [...]}` shape from the plan, each entry
   `{"filename": ..., "topic": ...}`, `topic` derived from each file's first line (empty
   string if the file is empty or its first line is blank). Excludes non-`.md` files.
   Returns `"workspace": []` unconditionally when the workspace is untrusted, without
   touching disk for that namespace.

4. **Implement `SearchMemoriesTool`**: `queries: list[str]`, same literal
   case-insensitive-substring-OR matching as `SearchScratchpadTool`
   (`klorb/src/klorb/tools/scratchpad/search.py`) — reuse its `re.escape`-and-join pattern
   rather than reimplementing it. Extends that pattern with the "filename itself is also a
   search subject" behavior the plan describes: when `filename` matches a query via
   `re.match` semantics, report the file's first non-blank line as the matching line
   regardless of whether it separately matches. Iterates every file in whichever
   namespace(s) are accessible (decide during implementation whether `namespace` is a tool
   parameter or the tool always searches both — the plan is silent on this; pin it down and
   record the decision, e.g. as an ADR if it's non-obvious, before or alongside
   implementation). Reported `filename` is always the bare in-namespace name, per the plan's
   explicit "never report the full path" requirement. Excludes the `workspace` namespace's
   files entirely (not just from results — from iteration) when untrusted, per
   `docs/specs/scratchpad.md`-adjacent precedent for how other tools skip inaccessible
   trees rather than filtering after the fact.

5. **Implement `ReadMemoryTool`**: `namespace: "global" | "workspace"` plus `filename`, plus
   the shared `start_line`/`end_line` properties from a held `self.read_file_core:
   ReadFileCore` (`klorb.tools.util.ReadFileCore`, constructed with
   `context.process_config.read_file_max_lines` — the same config value `ReadFileTool`/
   `ReadScratchpadTool` already use, not a new one). `apply()`: validate `namespace`, resolve
   `filename` via the `common.py` validator (raising for a separator/traversal attempt),
   check `tools.memory.readPermission` (see item 8), check the untrusted-workspace gate for
   `namespace="workspace"`, then delegate to `self.read_file_core.apply(path, args)` and add
   `namespace`/`filename` to the result.

6. **Implement `EditMemoryTool`**: same argument shape as `EditScratchpadTool` plus
   `namespace`/`filename`, delegating to a held `self.edit_file_core: EditFileCore`
   (constructed with `context.process_config.edit_file_drift_search_radius`, matching
   `EditFileTool`/`EditScratchpadTool`). Before delegating, enforce the "first line must not
   be blank" rule from the plan's "File format" section — this is new validation logic no
   existing `*FileCore` performs, so it belongs in this tool (or, if `CreateMemoryTool` needs
   the identical check, in a small shared helper in `memory/common.py` both tools call,
   rather than duplicated inline in each). Checks `tools.memory.editPermission` and the
   untrusted-workspace gate exactly as `ReadMemoryTool` does for reads.

7. **Implement `CreateMemoryTool`**: `namespace`/`filename`/`content`, delegating to
   `self.create_file_core: CreateFileCore` from item 1 for the "must not already exist,
   auto-create parent dirs, write, build result" mechanic, with the blank-first-line
   validation from item 6 applied to `content` before delegating. Checks
   `tools.memory.createPermission` (default `ask`) and the untrusted-workspace gate.

8. **Implement `DeleteMemoryTool`**: `namespace`/`filename`. New territory — no
   `resolve_within_workspace`/`evaluate_write` call applies (memories don't live under
   `readDirs`/`writeDirs`, same bypass rationale as scratchpad — see
   `docs/adrs/scratchpad-tools-bypass-permission-tables.md`) — implement directly against
   `Path.unlink()` after the namespace/filename validation, permission check
   (`tools.memory.deletePermission`, default `ask`), and untrusted-workspace gate, in that
   order, so a denied delete never touches the filesystem. Raise a clear, tool-appropriate
   error (not a raw `FileNotFoundError`) if `filename` doesn't exist.

9. **Wire the four permission flags into `ProcessConfig`**
   (`klorb/src/klorb/process_config.py`): add `memory_read_permission`/
   `memory_edit_permission`/`memory_create_permission`/`memory_delete_permission` fields
   (each typed `klorb.permissions.table.Verdict`, reusing that existing `Literal["deny",
   "ask", "allow"]` alias rather than redefining an equivalent one), defaults `"allow"`,
   `"allow"`, `"ask"`, `"ask"` respectively per the plan. Add the four corresponding
   `_CONFIG_KEY_MAP` entries (`"tools.memory.readPermission"` etc., following the existing
   dot-delineated lowerCamelCase convention right next to `"tools.scratchpad.contextLines"`)
   — see `CLAUDE.md`'s "On-disk key naming" note and `docs/specs/process-and-session-config.md`.
   Each tool above reads its own flag directly (a plain scalar comparison, not a
   `PermissionsTable` — there's no deny/ask/allow *list* per resource here, just one
   process-wide verdict per operation kind) and calls
   `klorb.permissions.table.raise_if_not_allowed(verdict, resource_description=...)` with no
   `path`/`is_write` (a structural ask/deny, the same shape `BashTool` already uses for a
   resource with nothing path-like to report — see `docs/specs/permissions.md`'s "Multi-item
   asks" section for the precedent).

10. **Untrusted-workspace gate**: add a small shared check in `memory/common.py` — something
    like `require_workspace_namespace_accessible(context, namespace)` — that every
    `workspace`-namespace operation calls first (ahead of, or alongside, the permission-flag
    check; decide and document the order, since a `PermissionAskRequired` vs. an outright
    "namespace inaccessible" error are observably different to a caller). Mirror the language
    the plan uses: `ListMemories`/`SearchMemories` report the workspace namespace as
    empty/unsearched rather than raising, per the plan's explicit wording; `Read`/`Create`/
    `Edit`/`DeleteMemory` raise outright ("instantly permission-denied without appeal to the
    user," per the plan) rather than surfacing a `PermissionAskRequired`.

11. **Register the new tools**: nothing extra needed beyond dropping them into
    `klorb/src/klorb/tools/memory/` — `ToolRegistry._discover_tools()`'s recursive package
    walk picks them up automatically, exactly as it already does for
    `klorb.tools.scratchpad`. Confirm this with a registry-level test (e.g. asserting
    `ToolRegistry(...).tools()` includes all six new tool names) rather than assuming.

12. **System prompt**: add a "Memories" section to
    `klorb/src/klorb/resources/system_prompts.d/default_sys.md`, alongside (not merged into)
    the existing "Use your scratchpad" section — explain the global-vs-workspace namespace
    distinction, that memories persist across sessions (unlike the scratchpad), and when to
    reach for one over the other. Keep the file-format rule (first line = topic, must not be
    blank) out of every tool's own `description()` and stated once here instead, mirroring
    how the drift-tolerant `EditFile`/`EditScratchpad` mechanic was centralized into this same
    file rather than repeated per tool (see docs/specs/scratchpad.md's "How it works" section,
    third bullet from the bottom).

13. **New spec**: once the above is implemented, write `docs/specs/memories.md` (this plan's
    "durable aspects," per `docs/plans/README-PLANS.md`'s top-level workflow) describing the
    namespace layout, file format, permission flags, and untrusted-workspace gating as
    current-state fact — then `git mv` this plan file into `docs/plans/archive/`.
