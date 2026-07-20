# Scratchpad

## Summary

Every `Session` owns a scratchpad: a single plain-text file, outside the model's own context
window, for notes on what's been tried and anything else worth keeping track of across a long
task -- explicitly not for tasks or todo items themselves, which are `TodoCreate`/`TodoNext`'s
job when chainlink task tracking is available (see docs/specs/chainlink-task-tracking.md).
`ReadScratchpad`, `EditScratchpad`, and `SearchScratchpad`
are the tools a model uses to read from, edit, and search it — named to match `ReadFile`/
`EditFile`'s verb-first convention. Unlike `ReadFile`/`EditFile`/`Grep`, none of the three take a
`filename`/`dirname` argument — each is pinned to the one file `Session.scratchpad.path` names,
so there's no `readDirs`/`writeDirs` permission check to perform: the scratchpad is
harness-managed session state, not a model-nameable path.

By default each `Session` gets its own scratchpad, created fresh in a new temporary directory.
A `Session` can instead be constructed with an existing scratchpad file to reuse — the mechanism
that lets several `Session`s (e.g. an operator and its subagents, once agent-team dispatch
exists — see `klorb.role.Role.repertoire` and docs/specs/roles-and-system-prompts.md's "Out of
scope") coordinate through one shared file rather than each keeping a private one.

## How it works

* `klorb/src/klorb/tools/scratchpad/` is a dedicated subpackage holding everything scratchpad-
  specific: `common.py` (the `Scratchpad` class and the `scratchpad_path(context)` tool helper),
  `read.py` (`ReadScratchpadTool`), `edit.py` (`EditScratchpadTool`), and `search.py`
  (`SearchScratchpadTool`). `klorb.tools.registry.ToolRegistry._discover_tools()` walks
  `klorb.tools` recursively (`pkgutil.walk_packages`, not just its immediate children) so tools
  defined inside this subpackage — or any future one — are discovered exactly like a top-level
  tool module, with no separate registration step. The subpackage's own `__init__.py`
  deliberately imports none of its `Tool` subclasses (see its docstring): they're found by the
  registry's own module walk, and importing them eagerly at package-import time would
  reintroduce the very import cycle `Scratchpad` (below) is structured to avoid.
* `klorb.tools.scratchpad.common.Scratchpad` owns creating, tracking, and cleaning up one
  session's scratchpad file, constructed with the same `scratchpad_path: str | None` a `Session`
  is constructed with:
  * Given a path, it's used as-is (as a `Path`), on the assumption the file already exists —
    the multi-session/shared-scratchpad case.
  * Given `None` (the default), a fresh directory is created via
    `tempfile.mkdtemp(prefix="klorb-scratchpad-")`, and `SCRATCHPAD_FILENAME` (`SCRATCHPAD.md`)
    inside it is `touch()`-ed into existence immediately — so `EditScratchpad`'s very first
    call has a real, zero-length file to edit rather than a `FileNotFoundError`.
  * `Scratchpad` has no runtime dependency on `klorb.tools.setup_context` (its `ToolSetupContext`
    reference in `scratchpad_path()` below is `TYPE_CHECKING`-only), so `klorb.session.Session`
    can hold one directly — `Session.__init__` does only `self.scratchpad =
    Scratchpad(scratchpad_path)` — without importing anything that imports `klorb.session` back,
    which a real import of `ToolSetupContext` (itself importing `Session`/`SessionConfig`) would.
  * `Session.__init__` also registers `self.scratchpad.cleanup` as a teardown (see
    `Session.register_teardown`/`close`), so a freshly created scratchpad's temp directory is
    removed (`shutil.rmtree(..., ignore_errors=True)`) once the session closes. `cleanup()` is a
    no-op for a caller-supplied `scratchpad_path`, tracked via a private `_owned_dir: Path |
    None` set only in the fresh-directory branch — a reused scratchpad's lifecycle belongs to
    whatever created it, not to this `Scratchpad`.
  * `Session.close()` only runs on an explicit session switch (`ReplApp.clear_session()`), so it
    never fires for the last active session on a normal TUI exit, and never at all on a crash or
    `SIGKILL`. So the fresh-directory branch *also* does `atexit.register(shutil.rmtree,
    scratchpad_dir, ignore_errors=True)` the moment it creates the directory — the same backstop
    `BashTool` uses for its spilled stdout/stderr directories. `cleanup()` stays the eager path
    (a switched-away session's directory goes right away rather than lingering until process
    exit); the `atexit` hook sweeps whatever `cleanup()` never got to. Both are
    `ignore_errors=True`, so the two firing for the same directory is harmless.
  * `Session` exposes the `Scratchpad` instance directly as a plain public field
    (`session.scratchpad`), not via a separate `scratchpad_path` property — `session.scratchpad
    .path` is exactly as much surface area as callers need, and `Session` itself does no
    scratchpad-specific work beyond owning that one field and registering its teardown.
  * The three tools read `session.scratchpad.path` via `scratchpad_path(context)` (also in
    `common.py`) — raising `ValueError` if `context.session` is `None` (e.g. a `ToolSetupContext`
    built directly, as most unit tests for other tools do), since there's no session-scoped file
    to point at.
* None of this touches `config.read_dirs`/`write_dirs`: the three tools read/write
  `session.scratchpad.path` directly, with no `readDirs`/`writeDirs` permission check at all —
  see docs/adrs/scratchpad-tools-bypass-permission-tables.md for why that's safe (the path is
  never model-supplied, so there's nothing for a permission check to protect against) and why a
  `Session`-level `allow` grant for the scratchpad directory was rejected (it would need to
  special-case the hard workspace-root boundary anyway, and several existing permission tests
  assert exact `read_dirs.allow`/`write_dirs.allow` contents that a blanket grant would corrupt).
  Reaching the scratchpad file through `ReadFile`/`EditFile`/`Bash` instead of the dedicated
  tools still goes through the ordinary tables like any other path, and is denied/asked exactly
  as it would be for any other path outside `readDirs`/`writeDirs`.
* `klorb.tools.util` (a package: `read_file_core.py` and `edit_file_core.py`, both re-exported
  from `__init__.py` so callers always write `from klorb.tools.util import ReadFileCore,
  EditFileCore` regardless of which submodule defines them) holds the two mechanics `ReadFile`/
  `ReadScratchpad` and `EditFile`/`EditScratchpad` share, so each pair is written and tested once
  rather than duplicated:
  * `ReadFileCore` (constructed with `max_lines: int`) implements the `start_line`/`end_line`
    paging and `"N|"` line-number-prefixed `content` both `ReadFileTool` and
    `ReadScratchpadTool` return. Each tool holds one as `self.read_file_core`, calls
    `self.read_file_core.apply(path, args)` for the bulk of its own `apply()`, and adds
    `filename` to the result itself if it has one (`ReadScratchpadTool` doesn't).
    `parameter_properties()` returns the shared `start_line`/`end_line` JSON-schema properties,
    so each tool's own `parameters()` only adds `filename` (or not) and its own `required` list
    around it.
  * `EditFileCore` (constructed with `drift_search_radius: int`) implements the full
    drift-tolerant row-extent substitution mechanic — argument validation, the line-range
    search/substitution algorithm (formerly a standalone `klorb.tools.line_range_edit` module,
    now folded in here since only `EditFileCore` calls it), and the result dict — behind one
    `apply(path, args, *, subject, reread_hint) -> dict[str, Any]` method. `EditFileTool` and
    `EditScratchpadTool` each hold one as `self.edit_file_core`, resolve their own `path` (a
    workspace-confined, permission-checked `filename` for `EditFileTool`; the fixed, unchecked
    `session.scratchpad.path` for `EditScratchpadTool`), and delegate to it — passing a
    `reread_hint` string substituted into error messages so they say "re-ReadFile foo.py" for
    `EditFileTool` or "re-ReadScratchpad your scratchpad" for `EditScratchpadTool`, as
    appropriate. Same `parameter_properties()` pattern as `ReadFileCore` for the shared
    `start_line`/`end_line`/`start_text`/`end_text`/`new_text`/`context_before`/`context_after`
    schema.
  * The lengthy "most common mistake"/drift/`"Ambiguous match"` explanation that used to live in
    both `EditFileTool.description()` and `EditScratchpadTool.description()` now lives once, in
    the default system prompt's "Editing with EditFile/EditScratchpad" section — each tool's own
    `description()` is a short pointer to it, not a restatement.
* `SearchScratchpadTool` (`klorb/src/klorb/tools/scratchpad/search.py`) takes `queries:
  list[str]` — one or more *literal* search strings, matched case-insensitively, never
  interpreted as regular expressions (each is `re.escape()`-d before being joined into one
  alternation pattern) — equivalent to running `grep -i -F -e 'seq1' -e 'seq2' ...` against the
  file. Each match is reported with `context.process_config.scratchpad_context_lines`
  (`ProcessConfig.scratchpad_context_lines`, default `2`) lines of surrounding context on each
  side; overlapping or adjacent matches' context windows are merged (mirroring `grep -C`'s own
  block-collapsing behavior) rather than returned as separately-overlapping results, so a cluster
  of nearby matches reads as one contiguous excerpt. The result's `lines` is a flat list of the
  same compact dense-format strings `GrepTool` returns (`"*42|matched text"` / `" 41|context
  text"`), built by the shared `klorb.tools.util.search_core` helpers; a break between two merged
  windows shows up only as a jump in the embedded line numbers, with no enclosing block wrapper
  (see the ADR `grep-search-tools-share-dense-line-core.md`). `detail_view()` caps the rendered
  `lines` list to 60 entries (`lines_omitted` reports how many more exist).
* The default system prompt (`klorb/src/klorb/resources/system_prompts.d/default_sys.md`)
  carries two sections relevant here: "Editing files (EditFile / EditScratchpad / EditMemory)"
  (the row-extent substitution mechanic, moved out of both tools' own `description()`s — see
  above) and "Scratchpad" (using the scratchpad for running notes — explicitly not tasks, which
  it points to `TodoCreate` for instead — and treating a shared scratchpad as a team coordination
  log for notes and findings, never for assigning tasks, when several agents share one).

## Configuration

* `tools.scratchpad.contextLines` (top-level `klorb-config.json` key, default `2`) — sets
  `ProcessConfig.scratchpad_context_lines`, consumed by `SearchScratchpadTool` — see
  [[process-and-session-config]].

## Out of scope

* There's no agent-team dispatch mechanism yet to actually spawn several `Session`s sharing one
  `scratchpad_path` — `Role.repertoire()` is still a placeholder (see
  docs/specs/roles-and-system-prompts.md's "Out of scope"). The constructor argument and the
  system prompt's team-coordination guidance are forward-looking, written against the day that
  mechanism exists, exactly like `SessionConfig.role_name`'s own "future subagent-spawning call
  site" note.
* A caller-supplied `scratchpad_path` is trusted as-is: `Scratchpad` doesn't verify the file
  exists, is readable/writable, or resolve it against any workspace boundary — the caller
  owns that file and is responsible for its lifecycle.
* No JSON `schema` envelope applies here (see docs/specs/persisted-json-schema-versioning.md):
  the scratchpad is free-form text the model itself writes and reads, not a structured file
  klorb parses back.
