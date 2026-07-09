# Scratchpad

## Summary

Every `Session` owns a scratchpad: a single plain-text file, outside the model's own context
window, for recording a running plan, notes on what's been tried, and anything else worth
keeping track of across a long task. `ScratchpadRead`, `ScratchpadWrite`, and `ScratchpadSearch`
are the tools a model uses to read from, edit, and search it. Unlike `ReadFile`/`EditFile`/
`Grep`, none of the three take a `filename`/`dirname` argument — each is pinned to the one file
`Session.scratchpad_path` names, so there's no `readDirs`/`writeDirs` permission check to
perform: the scratchpad is harness-managed session state, not a model-nameable path.

By default each `Session` gets its own scratchpad, created fresh in a new temporary directory.
A `Session` can instead be constructed with an existing scratchpad file to reuse — the mechanism
that lets several `Session`s (e.g. a coordinator and its subagents, once agent-team dispatch
exists — see `klorb.role.Role.repertoire` and docs/specs/roles-and-system-prompts.md's "Out of
scope") coordinate through one shared file rather than each keeping a private one.

## How it works

* `Session.__init__` (`klorb/src/klorb/session.py`) takes an optional `scratchpad_path: str |
  None` constructor argument and resolves it, once, via `_init_scratchpad`:
  * Given a path, it's used as-is (as a `Path`), on the assumption the file already exists —
    the multi-session/shared-scratchpad case.
  * Given `None` (the default), a fresh directory is created via
    `tempfile.mkdtemp(prefix="klorb-scratchpad-")`, and `SCRATCHPAD_FILENAME` (`SCRATCHPAD.md`)
    inside it is `touch()`-ed into existence immediately — so `ScratchpadWrite`'s very first
    call has a real, zero-length file to edit rather than a `FileNotFoundError`.
  * The resolved path is exposed as the read-only `Session.scratchpad_path` property, which the
    three tools read via `klorb.tools.scratchpad_common.scratchpad_path(context)` — raising
    `ValueError` if `context.session` is `None` (e.g. a `ToolSetupContext` built directly, as
    most unit tests for other tools do), since there's no session-scoped file to point at.
* None of this touches `config.read_dirs`/`write_dirs`: the three `Scratchpad*` tools read/write
  `Session.scratchpad_path` directly, with no `readDirs`/`writeDirs` permission check at all —
  see docs/adrs/scratchpad-tools-bypass-permission-tables.md for why that's safe (the path is
  never model-supplied, so there's nothing for a permission check to protect against) and why a
  `Session`-level `allow` grant for the scratchpad directory was rejected (it would need to
  special-case the hard workspace-root boundary anyway, and several existing permission tests
  assert exact `read_dirs.allow`/`write_dirs.allow` contents that a blanket grant would corrupt).
  Reaching the scratchpad file through `ReadFile`/`EditFile`/`Bash` instead of the dedicated
  tools still goes through the ordinary tables like any other path, and is denied/asked exactly
  as it would be for any other path outside `readDirs`/`writeDirs`.
* `ScratchpadReadTool` (`klorb/src/klorb/tools/scratchpad_read.py`) mirrors `ReadFileTool`
  (see [[tool-framework]]): `start_line`/`end_line` paging, the same `"N|"` line-number-prefixed
  `content`, and the same `context.process_config.read_file_max_lines` per-call cap — just with
  no `filename` parameter and no permission check.
* `ScratchpadWriteTool` (`klorb/src/klorb/tools/scratchpad_write.py`) mirrors `EditFileTool`'s
  row-extent substitution contract exactly — `start_line`/`end_line`/`start_text`/`end_text`/
  `new_text`/`context_before`/`context_after`, the same drift tolerance (bounded by
  `context.process_config.edit_file_drift_search_radius`), the same empty-file/insert/delete
  conventions — by calling the same underlying mechanic `EditFileTool` does,
  `klorb.tools.line_range_edit.resolve_line_range_edit`, rather than reimplementing it. That
  function was factored out of `EditFileTool` for exactly this reuse: it takes the subject's
  current lines plus every edit argument and a `reread_hint` string (substituted into error
  messages so they say "re-ReadFile foo.py" for `EditFileTool` or "re-ScratchpadRead your
  scratchpad" for `ScratchpadWriteTool`, as appropriate) and returns the resolved span plus the
  substituted line list; each tool then handles its own I/O and permission checking (or lack
  thereof) around that shared core.
* `ScratchpadSearchTool` (`klorb/src/klorb/tools/scratchpad_search.py`) takes `queries: list[str]`
  — one or more search sequences, each a regular expression — combines them into a single
  case-insensitive alternation (`(?:seq1)|(?:seq2)|...`), and matches it line-by-line against the
  scratchpad, equivalent to running `grep -i -e 'seq1' -e 'seq2' ...` against the file. Each
  match is reported with `context.process_config.scratchpad_context_lines`
  (`ProcessConfig.scratchpad_context_lines`, default `2`) lines of surrounding context on each
  side; overlapping or adjacent matches' context windows are merged into one block (mirroring
  `grep -C`'s own block-collapsing behavior) rather than returned as separately-overlapping
  results, so a cluster of nearby matches reads as one contiguous excerpt.
* Tool discovery needs no wiring: like every other `Tool`, all three are found automatically by
  `ToolRegistry._discover_tools()`'s package scan (see [[tool-framework]]) — a new tool module
  under `klorb/src/klorb/tools/` with one concrete `Tool` subclass is enough.
* The default system prompt (`klorb/src/klorb/resources/system_prompts.d/default_sys.md`, "Use
  your scratchpad" section) tells the model to use the scratchpad for its own running notes and,
  when several agents share one scratchpad, to treat it as a team coordination log — writing
  what it's doing and checking it for teammates' updates before acting.

## Configuration

* `tools.scratchpad.contextLines` (top-level `klorb-config.json` key, default `2`) — sets
  `ProcessConfig.scratchpad_context_lines`, consumed by `ScratchpadSearchTool` — see
  [[process-and-session-config]].

## Out of scope

* There's no agent-team dispatch mechanism yet to actually spawn several `Session`s sharing one
  `scratchpad_path` — `Role.repertoire()` is still a placeholder (see
  docs/specs/roles-and-system-prompts.md's "Out of scope"). The constructor argument and the
  system prompt's team-coordination guidance are forward-looking, written against the day that
  mechanism exists, exactly like `SessionConfig.role_name`'s own "future subagent-spawning call
  site" note.
* A freshly created scratchpad's `tempfile.mkdtemp()` directory is never cleaned up by
  `Session.close()` or an `atexit` hook — it outlives the process, unlike `BashTool`'s spilled
  stdout/stderr directories, which register `atexit.register(shutil.rmtree, ...)`. A scratchpad
  is meant to be inspectable after the fact (e.g. to see what an agent was tracking), not
  transient output.
* A caller-supplied `scratchpad_path` is trusted as-is: `Session` doesn't verify the file
  exists, is readable/writable, or resolve it against any workspace boundary — the caller
  owns that file and is responsible for its lifecycle.
* No JSON `schema` envelope applies here (see docs/specs/persisted-json-schema-versioning.md):
  the scratchpad is free-form text the model itself writes and reads, not a structured file
  klorb parses back.
