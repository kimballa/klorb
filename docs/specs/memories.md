# Memories

## Summary

A memory is a markdown file an agent writes to itself, to record something worth recalling in
a later session — a durable fact about the user, a project convention, a decision and why it
was made. Unlike the scratchpad (see docs/specs/scratchpad.md), which is discarded once a
session closes, memories persist across sessions. `ListMemories`, `SearchMemories`,
`ReadMemory`, `EditMemory`, `CreateMemory`, and `ForgetMemory` are the tools a model uses to
enumerate, find, read, and write them.

Every memory lives in one of two namespaces:

* `global` memories live under `KLORB_DATA_DIR / "memories"` (default
  `~/.local/share/klorb/memories/`) and apply across every workspace.
* `workspace` memories live under `${workspace_root}/.klorb/memories/` and apply only within
  that workspace.

Both namespaces are flat: a memory tool's `filename` argument is a bare name (e.g.
`user-preferences.md`), never a path, and no memory tool reads or writes a subdirectory of
either namespace.

## How it works

* `klorb/src/klorb/tools/memory/` is a dedicated subpackage, mirroring
  `klorb/src/klorb/tools/scratchpad/`'s own layout: `common.py` (namespace resolution,
  `filename` validation, the untrusted-workspace gate, and the blank-first-line-rejection
  helper) plus one module per tool (`list_memories.py`, `search_memories.py`,
  `read_memory.py`, `edit_memory.py`, `create_memory.py`, `forget_memory.py`). Its
  `__init__.py` deliberately imports none of these `Tool` subclasses, for the same
  import-cycle reason `klorb.tools.scratchpad`'s own `__init__.py` doesn't (see that
  subpackage's docstring) — `klorb.tools.registry.ToolRegistry._discover_tools()`'s recursive
  `pkgutil.walk_packages` walk finds them anyway.
* `klorb.tools.memory.common.memory_namespace_dir(context, namespace)` resolves a namespace to
  its directory without creating it — neither namespace directory is created eagerly (there is
  no per-session provisioning step, unlike `Scratchpad`'s own `tempfile.mkdtemp()`); each is
  created on demand, at first write, by `CreateFileCore.apply()`'s own
  `path.parent.mkdir(parents=True, exist_ok=True)`.
* `klorb.tools.memory.common.validate_memory_filename(filename, namespace_dir)` is the single
  validator every tool calls before touching disk: it rejects a `filename` containing a path
  separator, rejects one not ending in `.md` (never silently normalized by appending it), and
  resolves the result via `klorb.permissions.directory_access.canonicalize_dir` — the same
  primitive `readDirs`/`writeDirs` rule paths use — as a second, defense-in-depth check that
  the resolved path is a direct child of `namespace_dir` and never escapes it (e.g. via `..` or
  a symlink).
* **Memory tools bypass `readDirs`/`writeDirs` entirely**, the same design as the Scratchpad
  tools (see docs/adrs/scratchpad-tools-bypass-permission-tables.md): `filename` is a bare name
  within a harness-resolved namespace directory, never a model-supplied path into the rest of
  the filesystem, so there is nothing for those tables to protect against. Instead, each
  operation kind (read, edit, create, delete) has its own flat `Verdict` (`"deny"`/`"ask"`/
  `"allow"`) on `ProcessConfig`, checked via a single, path-less
  `klorb.permissions.table.raise_if_not_allowed(verdict, resource_description=...)` call — a
  structural ask/deny with no `path`/`command`, the same shape `BashTool` uses for a forced-ask
  reason with no filesystem resource of its own (see docs/specs/permissions.md's "Multi-item
  asks" section).
* `ReadMemoryTool`/`EditMemoryTool` delegate their line-range mechanics to
  `klorb.tools.util.ReadFileCore`/`EditFileCore`, the same cores `ReadFile`/`EditFile`/
  `ReadScratchpad`/`EditScratchpad` use — see docs/adrs/read-edit-file-scratchpad-share-core-via-composition.md.
  `CreateMemoryTool` similarly delegates to `klorb.tools.util.CreateFileCore`, the file-creation
  mechanic extracted from `CreateFileTool` (which now holds one too) so both tools share it
  rather than duplicating the "already exists / create missing parents / write" logic.
  `ForgetMemoryTool` has no existing core to share — no other tool deletes a harness-resolved
  file — so it calls `Path.unlink()` directly, after every validation/permission/trust check.
* **File format**: a memory is an ordinary markdown file whose first line is its *topic* — a
  one-line summary `ListMemoriesTool`/`SearchMemoriesTool` show without opening the file. The
  first line must never be blank: `CreateMemoryTool` validates `content`'s first line up front
  (an empty or whitespace-only `content` is rejected before any disk I/O — there's no way to
  create a topic-less memory and fill in the topic with a later edit), and `EditMemoryTool`
  enforces the same invariant on every edit. Because `EditFileCore.apply()` resolves
  `start_line`/`end_line` drift and writes the file in one step, there's no way to predict the
  resulting first line without either duplicating its drift-resolution algorithm or checking
  after the fact — `EditMemoryTool` delegates as normal, then re-reads the file's first line
  and, if it's now blank (whether the edit targeted line 1 directly, or deleted it and promoted
  a blank line 2), either rewrites the file's pre-edit content back or, if this same call just
  auto-created the memory (see below), deletes it — there's no pre-edit content to restore in
  that case — and raises `ValueError` rather than leaving a topic-less memory on disk.
* `EditMemoryTool` no longer requires a memory to already exist: a `namespace`/`filename` pair
  with nothing on disk is treated exactly like `EditFileTool`'s nonexistent-file case — the
  empty-subject insert shape (`start_line=1, end_line=0, start_text="", end_text=""`)
  auto-creates it via the same `EditFileCore.apply()` path (see docs/specs/tool-framework.md and
  docs/adrs/edit-file-auto-creates-via-empty-subject-insert-shape.md), so a model that already
  knows the target namespace/filename combo doesn't need a separate `CreateMemory` call first.
  Any other shape against a nonexistent memory raises `FileNotFoundError` naming `CreateMemory`
  as the tool to use instead of the bare OS error.
* `ListMemoriesTool` (no arguments) returns `{"global": [...], "workspace": [...]}`, each entry
  `{"filename": ..., "topic": ...}` (`topic` is `""` for an empty file or a blank/whitespace-only
  first line). It excludes non-`.md` files and dotfiles, and does not recurse into
  subdirectories.
* `SearchMemoriesTool` takes `queries: list[str]` — matched as a literal, case-insensitive
  substring (never a regular expression), the same `klorb.tools.util.search_core` construction
  `GrepTool`/`SearchScratchpadTool` use — and always searches every accessible namespace; there
  is no `namespace` argument to narrow it, matching `ListMemories`' own "always both" shape. Each
  matching file is reported once in `results` as `{namespace, filename, lines}`, where `lines` is
  a flat list of the shared dense-format strings (`"*42|matched text"`, a leading `*`/space match
  marker plus 1-based line number); there is no surrounding context (only the matching lines are
  listed). A file's own `filename` is also a search subject: a query matching `filename` returns
  that file even if none of its lines do, listing its first non-blank line as a single unmatched
  (` `-prefixed) line; a file matched by both its filename and real content is reported once,
  using the real content matches. `match_count` counts individual matching lines, plus one for
  each filename-only hit (see the ADR `grep-search-tools-share-dense-line-core.md`).
* **Untrusted-workspace gating**: `workspace` memories are inaccessible in an untrusted
  workspace (see `klorb.workspace.Workspace.trusted`). `ListMemories`/`SearchMemories` report
  the `workspace` namespace as empty (or skip it entirely during iteration) rather than
  raising — the same "quietly report nothing" behavior an untrusted `readDirs` boundary doesn't
  use, but chosen here since there's no single resource to ask about, just an entire namespace
  to omit. `ReadMemory`/`EditMemory`/`CreateMemory`/`ForgetMemory` instead raise `PermissionError`
  outright for a `workspace`-namespace call in an untrusted workspace — checked *before* the
  operation's own `tools.memory.*Permission` verdict, so an untrusted-workspace denial is never
  observable as a `PermissionAskRequired` (which would imply a user could approve their way past
  it); `global` memories are never affected by workspace trust.
* The default system prompt (`klorb/src/klorb/resources/system_prompts.d/default_sys.md`) has a
  "Memories" section, alongside (not merged into) "Use your scratchpad", explaining the
  namespace distinction, the topic-first-line convention, and when to reach for a memory over
  the scratchpad.

## Configuration

Four process-level `Verdict` (`"deny"`/`"ask"`/`"allow"`) flags, one per operation kind:

* `tools.memory.readPermission` (default `"allow"`) — governs `ListMemories`, `SearchMemories`,
  and `ReadMemory`.
* `tools.memory.editPermission` (default `"allow"`) — governs `EditMemory`.
* `tools.memory.createPermission` (default `"ask"`) — governs `CreateMemory`.
* `tools.memory.deletePermission` (default `"ask"`) — governs `ForgetMemory`.

Each sets the correspondingly-named `ProcessConfig` field (`memory_read_permission`,
`memory_edit_permission`, `memory_create_permission`, `memory_delete_permission`) via
`PROCESS_KEY_MAP` — see docs/specs/process-and-session-config.md. Read/edit default to
`"allow"` since a memory is harness-managed, session-spanning state rather than a workspace
file a model can redirect through (the same reasoning `readPermission`'s Scratchpad-tool
counterpart never needed, since those tools skip permission tables outright); create/delete
default to `"ask"` since those are the less-reversible operations.

## Out of scope

* There's no size cap, rotation, or pruning policy for memory files — a memory grows exactly as
  large as the model chooses to write, the same as the scratchpad.
* No JSON `schema` envelope applies (see docs/specs/persisted-json-schema-versioning.md): a
  memory is free-form markdown text the model itself writes and reads, not a structured file
  klorb parses back.
* There's no cross-namespace or cross-workspace memory sharing/sync mechanism (e.g. syncing a
  `workspace` memory up to `global`, or between machines) — each namespace directory is a plain
  local directory with no further indirection.
