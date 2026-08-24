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

`MEMORY.md` is a reserved filename, in each namespace, meant as a freeform table of contents
over that namespace's other memory files rather than a place to record full detail — see
"The MEMORY.md table of contents" below.

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
  tools (see docs/adrs/00089-scratchpad-tools-bypass-permission-tables.md): `filename` is a bare name
  within a harness-resolved namespace directory, never a model-supplied path into the rest of
  the filesystem, so there is nothing for those tables to protect against. Instead: a `read` is
  always allowed, in both namespaces, with no permission check at all; a `global`-namespace
  write or delete is likewise always allowed; a `workspace`-namespace write (`CreateMemory`/
  `EditMemory`) or delete (`ForgetMemory`) consults its own flat `Verdict`
  (`"deny"`/`"ask"`/`"allow"`) on `SessionConfig`, raised via
  `klorb.permissions.table.raise_if_not_allowed(verdict, resource_description=..., memory=(access,
  filename))` — building a real `klorb.permissions.resource.MemoryResource`, so a `"ask"` verdict
  flows through the ordinary interactive ask panel, session/workspace/homedir grant, and
  `permission_framework="auto"` handling every other persistable resource kind gets, rather than
  the structural, non-persistable ask/deny `StructuralResource` represents (see
  docs/specs/permissions.md's "Multi-item asks" section).
* `ReadMemoryTool`/`EditMemoryTool` delegate their line-range mechanics to
  `klorb.tools.util.ReadFileCore`/`EditFileCore`, the same cores `ReadFile`/`EditFile`/
  `ReadScratchpad`/`EditScratchpad` use — see docs/adrs/00088-read-edit-file-scratchpad-share-core-via-composition.md.
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
  enforces the same invariant on every edit. Because `EditFileCore.apply()` resolves the match
  and writes the file in one step, there's no way to predict the resulting first line without
  either duplicating its matching algorithm or checking after the fact — `EditMemoryTool`
  delegates as normal, then re-reads the file's first line and, if it's now blank (whether the
  edit targeted line 1 directly, or deleted it and promoted a blank line 2), either rewrites the
  file's pre-edit content back or, if this same call just auto-created the memory (see below),
  deletes it — there's no pre-edit content to restore in that case — and raises `ValueError`
  rather than leaving a topic-less memory on disk.
* `EditMemoryTool` no longer requires a memory to already exist: a `namespace`/`filename` pair
  with nothing on disk is treated exactly like `EditFileTool`'s nonexistent-file case —
  `old_text=""` auto-creates it via the same `EditFileCore.apply()` path (see
  docs/specs/tool-framework.md), so a model that already knows the target namespace/filename
  combo doesn't need a separate `CreateMemory` call first. Any other shape against a nonexistent
  memory raises `FileNotFoundError` naming `CreateMemory` as the tool to use instead of the bare
  OS error.
* `ListMemoriesTool` (no arguments) returns `{"global": [...], "workspace": [...]}`, each entry
  `{"filename": ..., "topic": ...}` (`topic` is `""` for an empty file or a blank/whitespace-only
  first line). It excludes non-`.md` files and dotfiles, and does not recurse into
  subdirectories.
* `SearchMemoriesTool` takes `queries: list[str]` — matched as a literal, case-insensitive
  substring (never a regular expression), the same `klorb.tools.util.search_core` construction
  `GrepTool`/`SearchScratchpadTool` use — plus an optional `namespace` (`"global"`/`"workspace"`/
  `"all"`, default `"all"`) narrowing which namespace is searched; unlike `ListMemories`, this one
  doesn't always cover both. Each matching file is reported once in `results` as `{namespace,
  filename, lines}`, where `lines` is a flat list of the shared dense-format strings
  (`"*42|matched text"`, a leading `*`/space match marker plus 1-based line number); there is no
  surrounding context for a literal match (only the matching lines are listed). A file's own
  `filename` is also a search subject: a query matching `filename` returns that file even if none
  of its lines do, listing its first non-blank line as a single unmatched (` `-prefixed) line; a
  file matched by both its filename and real content is reported once, using the real content
  matches. `match_count` counts individual matching lines, plus one for each filename-only hit
  (see the ADR `grep-search-tools-share-dense-line-core.md`).
* On top of the literal search above, `SearchMemoriesTool` folds in up to `SEMANTIC_TOP_K` (5)
  semantic hits — chunks related to any query by meaning rather than exact wording — from the
  requested namespace's `memories-global`/`memories-workspace` catalogs, both of which
  `context.session.workspace_indexer` covers (see docs/specs/local-search-index.md's "Indexing:
  `WorkspaceIndexer`" section), via the same `klorb.tools.util.semantic_search_core.
  SemanticSearchCore` `SemanticSearch` uses. Only a hit scoring at least `SEMANTIC_MIN_SCORE`
  (equivalent to ranking in the top 3 of at least one of the fused lexical/vector lists — a high
  bar, appropriate since a session isn't expected to accumulate many memory files) surfaces; a
  semantic entry carries an additional `score` field and lists its matched chunk's line span (all
  lines marked). A file already reported via a literal match is never duplicated as a separate
  semantic entry. Unlike `SemanticSearch`, an unavailable index (feature disabled, embedding model
  not installed, or `context.session.workspace_indexer` being `None`) is never an error — the
  semantic hits it would have added are simply omitted, since the literal search above always
  still runs.
* **Untrusted-workspace gating**: `workspace` memories are inaccessible in an untrusted
  workspace (see `klorb.workspace.Workspace.trusted`). `ListMemories`/`SearchMemories` report
  the `workspace` namespace as empty (or skip it entirely during iteration) rather than
  raising — the same "quietly report nothing" behavior an untrusted `readDirs` boundary doesn't
  use, but chosen here since there's no single resource to ask about, just an entire namespace
  to omit. `ReadMemory`/`EditMemory`/`CreateMemory`/`ForgetMemory` instead raise `PermissionError`
  outright for a `workspace`-namespace call in an untrusted workspace — checked *before* the
  operation's own `tools.memory.*Permission` verdict, so an untrusted-workspace denial is never
  observable as a `PermissionAskRequired` (which would imply a user could approve their way past
  it); `global` memories are never affected by workspace trust. This means "workspace reads are
  always allowed" is itself conditioned on trust: an untrusted workspace still fails every
  `workspace`-namespace memory operation, including a read, closed up front.
* The default system prompt (`klorb/src/klorb/resources/system_prompts.d/default_sys.md`) has a
  "Memories" section, alongside (not merged into) "Use your scratchpad", explaining the
  namespace distinction, the topic-first-line convention, and when to reach for a memory over
  the scratchpad.
* `Session._build_memories_interjection()` (`klorb.session.mixins.memory.SessionMemoryMixin`)
  calls `ListMemories` once, on the very first turn, and prepends its `filename`/`topic`
  catalog as a `<SystemInterjection subject="Memories">` — the `workspace` section is omitted
  entirely in an untrusted workspace, and an empty namespace gets a `CreateMemory` nudge instead
  of an empty list. This saves the model an initial `ListMemories` round trip; any
  `ListMemories` failure just drops the interjection rather than surfacing an error.

### The MEMORY.md table of contents

`klorb.tools.memory.common.MEMORY_TOC_FILENAME` (`MEMORY.md`) is a reserved filename, in each
namespace, with no special validation of its own beyond the ordinary `filename` rules — a model
creates and edits it with `CreateMemory`/`EditMemory` exactly like any other memory. Two
mechanics treat it specially:

* `SessionMemoryMixin._read_memory_toc()` reads its leading `MEMORY_TOC_AUTO_READ_LINES` (50)
  lines, per namespace, via `ReadMemory`, and folds that content, wrapped in a
  `<MemoryTableOfContents namespace="...">` tag, directly into the `Memories` interjection
  alongside the `filename`/`topic` catalog — so a model sees its table of contents without
  spending a tool call on it. A missing `MEMORY.md` contributes nothing to the interjection; a
  `ReadMemory` failure is logged and dropped, the same as a `ListMemories` failure. When
  `ReadMemory` reports `truncated`, a trailing note names the exact `ReadMemory` call (with
  `start_line=MEMORY_TOC_AUTO_READ_LINES + 1`) to read the rest, and nudges the model to move
  content out via `EditMemory`/`CreateMemory` instead.
* `klorb.tools.memory.common.memory_toc_overflow_warning()` returns a warning string once
  `MEMORY.md` reaches `MEMORY_TOC_WARN_LINES` (45) lines — `CreateMemoryTool`/`EditMemoryTool`
  return it from their `Tool.call_interjection()` override, so it reaches the model as a
  `<SystemInterjection subject="CreateMemory"/"EditMemory">` on that same call's response, since
  content past line 50 stops being picked up by the interjection automatically. It names the
  exact `EditMemory`/`CreateMemory` calls (including the current `namespace`) to compact
  `MEMORY.md` itself or move detail into another memory file.

## Configuration

Two session-level `Verdict` (`"deny"`/`"ask"`/`"allow"`) flags, each governing only the
`workspace` namespace:

* `tools.memory.writePermission` (default `"ask"`) — governs `CreateMemory` and `EditMemory`,
  as one unified access level.
* `tools.memory.deletePermission` (default `"ask"`) — governs `ForgetMemory`.

Each sets the correspondingly-named `SessionConfig` field (`memory_write_permission`,
`memory_delete_permission`) via `SESSION_KEY_MAP` — see docs/specs/process-and-session-config.md.
They live on `SessionConfig`, not `ProcessConfig`, like every other ask-able rule table
(`skill_rules`, `command_rules`, ...), so a `"session"`-scope grant from the interactive ask panel
only affects the live session rather than every session in the process.

There is no configurable `readPermission`, and no `global`-namespace equivalent of either flag:
a `read`, in either namespace, and any `global`-namespace write or delete, is unconditionally
allowed and never consults a permission table at all — a `global` memory lives entirely under the
user's own home directory, with no workspace-supplied content to distrust, and a read can't
itself persist anything a hostile workspace planted.

## Out of scope

* There's no size cap, rotation, or pruning policy for memory files — a memory grows exactly as
  large as the model chooses to write, the same as the scratchpad.
* No JSON `schema` envelope applies (see docs/specs/persisted-json-schema-versioning.md): a
  memory is free-form markdown text the model itself writes and reads, not a structured file
  klorb parses back.
* There's no cross-namespace or cross-workspace memory sharing/sync mechanism (e.g. syncing a
  `workspace` memory up to `global`, or between machines) — each namespace directory is a plain
  local directory with no further indirection.
