# Tool framework

## Summary

A `Tool` is a unit of functionality a model can be offered, and asked to invoke, while
answering a prompt (a.k.a. "function calling"). `ToolRegistry` discovers `Tool`
implementations and acts as a factory for them, building the tool definitions sent to the
model alongside a prompt and instantiating a fresh `Tool` per call. This is a framework-level
feature: individual tools (file search, shell exec, etc.) will be added under
`klorb/src/klorb/tools/` as separate modules later and picked up automatically. See
[[session-and-turns]] for how `Session` actually wires a `ToolRegistry` into the turn loop.

## How it works

* `klorb.tools.setup_context.ToolSetupContext` (`klorb/src/klorb/tools/setup_context.py`) is
  a pydantic `BaseModel` holding `process_config: ProcessConfig` and
  `session_config: SessionConfig` — references to the actual config objects, not individual
  settings pre-extracted from them. `session_config` is the *live* `Session.config`, not
  `process_config.session` (only the template a session's config is copied from — see
  [[process-and-session-config]]). See
  [the ToolSetupContext ADR](../adrs/tool-setup-context-carries-process-and-session-config.md)
  for why it holds the config objects themselves rather than flattened fields.
* `klorb.tools.tool.Tool` (`klorb/src/klorb/tools/tool.py`) is an abstract base class. Its
  `__init__(self, context: ToolSetupContext)` is concrete (not abstract) and imposes a
  standard constructor on every subclass: a `Tool` is always constructed with exactly one
  `ToolSetupContext` argument, never tool-specific constructor arguments, so `ToolRegistry`
  can instantiate any `Tool` subclass uniformly. A subclass that needs to configure itself
  (e.g. a per-call line limit) pulls the relevant setting out of `context` in its own
  `__init__`, after calling `super().__init__(context)`. The stored context is available to
  subclasses via the `context` property. Concrete tools implement:
  * `name() -> str` — the tool's name, as reported to the model.
  * `description() -> str` — the tool's description, as reported to the model.
  * `parameters() -> dict[str, Any] | type[BaseModel]` — the tool's argument schema, either
    a raw JSON schema dict or a pydantic `BaseModel` subclass.
  * `apply(args: dict[str, Any]) -> Any` — runs the tool given a dict of arguments (as
    returned by the model) and returns the result.

  Two further methods are concrete, not abstract, so every `Tool` has a usable default and a
  subclass only overrides them for a nicer rendering:
  * `summary(args, result=None, error=None) -> str` — a one-line, human-friendly description
    of one call to this tool (e.g. `"Edit file: foo.py (+15/-6)"`), shown by default wherever
    a UI renders tool call activity (see [[terminal-repl]]). `error is None` means the call
    succeeded (even if `result` is itself `None`); `error is not None` means it failed and
    `result` is meaningless — this is the sole success/failure discriminant. Defaults to
    `default_tool_call_summary()`.
  * `detail_view(args, result=None, error=None) -> str` — a fuller rendering of the call's
    arguments and result/error, shown when a UI's user asks for more than `summary()` gives.
    Same success/failure contract as `summary()`. Defaults to `default_tool_call_detail()`
    (pretty-printed JSON of `args` alongside `result` or `error`); overridden only when that's
    a poor fit, e.g. to truncate a long field via the `truncate_lines()` helper instead of
    dumping it in full.

  `default_tool_call_summary()`/`default_tool_call_detail()` (both in `klorb/src/klorb/tools/
  tool.py`) are also what a consumer falls back to for a tool call whose name isn't recognized
  by a `ToolRegistry` (so there's no `Tool` instance to call `.summary()`/`.detail_view()` on)
  — one implementation of the default rendering, not duplicated between the base class and
  that fallback path. See
  [the raw-callback-data ADR](../adrs/render-tool-calls-via-raw-callback-data.md) for how a
  `Session`-reported tool call actually reaches these methods.
* `klorb.tools.registry.ToolRegistry` (`klorb/src/klorb/tools/registry.py`) is constructed
  with `(process_config: ProcessConfig, session_config: SessionConfig, package: ModuleType =
  klorb.tools)` — held by reference, not copied, so later changes to either (e.g. a TUI
  command palette mutating `session_config` in place) are picked up by tools instantiated
  afterward. It discovers `Tool` subclasses by walking `package`'s modules with
  `pkgutil.iter_modules`, importing each, and collecting concrete (non-abstract) `Tool`
  subclasses defined directly in that module — exactly once, in `__init__`; it never
  re-scans. By default it scans the `klorb.tools` package itself, so dropping a new module
  containing a `Tool` subclass into `klorb/src/klorb/tools/` is enough to register it — no
  manual registration step is required. A different package can be passed to the
  constructor (used by tests to scan a fixture package instead).
  * `instantiate_tool(name: str) -> Tool` — the factory method: builds a fresh
    `ToolSetupContext` from the registry's current `process_config`/`session_config` and
    constructs a brand new instance of the named tool's class, raising `KeyError` if no tool
    with that name was discovered. Called once per requested tool call by
    `Session._run_tool_calls` (see [[session-and-turns]]), so a tool never carries state over
    between calls. See
    [the fresh-instance-per-call ADR](../adrs/tool-registry-instantiates-a-fresh-tool-per-call.md).
  * `tools() -> list[Tool]` — a freshly-instantiated `Tool` for every discovered tool.
  * `tool_definitions() -> list[dict[str, Any]]` — builds the OpenAI/OpenRouter
    function-calling `tools` array: each entry is
    `{"type": "function", "function": {"name", "description", "parameters"}}`, with
    pydantic parameter schemas converted to JSON schema via `model_json_schema()`.

## Built-in tools

* `klorb.tools.read_file.ReadFileTool` (`klorb/src/klorb/tools/read_file.py`), name
  `ReadFile`. Reads a text file given a mandatory `filename`, and optional 1-indexed
  `start_line`/`end_line` (inclusive). `start_line` of `0` or omitted means start at the
  beginning of the file; `end_line` omitted means read up to the per-call line cap from
  `start_line`. At most `context.process_config.read_file_max_lines` lines (default
  `process_config.DEFAULT_READ_FILE_MAX_LINES`, 200 — the sole canonical source of this
  default; `klorb.tools.read_file` has no constant of its own) are returned per call
  regardless of the requested range, so an agent pages through larger files with successive
  calls. The result is a dict: `filename`, the
  actual `start_line`/`end_line` returned, the file's `total_lines`, a `truncated` flag (true
  when more content exists past `end_line`), and `content` — a single string with one
  `"N|line text"` entry per line, newline-separated. `summary()` names the file and the
  returned line range; `detail_view()` caps `content` to 8 lines via `truncate_lines()`, since
  a full result can be up to `read_file_max_lines` (200 by default) lines.
* `klorb.tools.edit_file.EditFileTool` (`klorb/src/klorb/tools/edit_file.py`), name
  `EditFile`. Replaces the inclusive 1-indexed line range `start_line`..`end_line` of an
  existing text file with `new_text`, after locating the mandatory `start_text`/`end_text`
  arguments at or near those lines. `start_line`/`end_line` are a location hint, not a hard
  requirement: if they don't match exactly, `apply()` searches within
  `context.process_config.edit_file_drift_search_radius` lines (default
  `process_config.DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS`, 20 — the sole canonical source of
  this default) for a unique nearby location where `start_text`/`end_text` still match at the
  same relative span, and edits there instead, reporting the correction back (see the response
  shape below). No match within that radius still raises `ValueError` naming the actual
  content at the hint, signaling stale line numbers (e.g. from an earlier edit shifting
  everything below it) rather than corrupting the file; more than one match raises a
  distinctly-worded `"Ambiguous match"` `ValueError` listing the candidate lines along with
  each one's actual nearby content as a ready-to-use `context_before=...`/`context_after=...`
  value, adaptively as many lines on each side as needed to tell every candidate apart (fewer
  near a file's start/end, and no fixed cap — see `_minimal_disambiguating_window`), resolvable
  by retrying with a closer `start_line` or the optional `context_before`/`context_after`
  arguments (checked against every candidate whenever supplied) — the preview lets a model copy
  that value verbatim for the location it means rather than reconstruct it from a separate
  `ReadFile` call. Omitting `context_before`/`context_after` means "don't check this side";
  passing the empty string `""` instead is a distinct, checked assertion that there's genuinely
  nothing on that side (the target is the file's actual first/last line). An out-of-bounds hint
  (`end_line` past the file's end, etc.) raises immediately with no search attempted. See
  [the drift-tolerance ADR](../adrs/edit-file-tolerates-bounded-line-drift-via-local-candidate-search.md).
  There is no separate insert or delete tool: insert without deleting by setting
  `start_line == end_line` and folding that line's original text into `new_text`; delete by
  passing an empty `new_text`. The one exception is an empty file (`total_lines == 0`), which
  has no anchor line to replace — the only valid call there is `start_line=1, end_line=0,
  start_text="", end_text=""`. See
  [the insert/delete ADR](../adrs/edit-file-covers-insert-and-delete-via-replace-range.md).
  Trailing-newline handling: an edit that doesn't touch the file's last line preserves whatever
  trailing-newline state the file already had; an edit whose `end_line` reaches the end of the
  file (including the empty-file case) always terminates the file with a single trailing `\n`
  if any content remains, none otherwise. The result is a dict: `filename`,
  `requested_start_line`/`requested_end_line` (echoing the input), the edited region's
  `start_line`/`end_line` (where the edit actually landed, renumbered to reflect what was
  actually written — possibly different from what was requested), `line_hint_matched` (false
  if a drift relocation happened), the file's new `new_total_lines`, and `content` — the
  changed region in `ReadFile`'s `"N|text"` format, so the model can see the result without a
  follow-up `ReadFile` call. `summary()` reports a `"+A/-R"` line-diff count computed from the
  call's own `start_line`/`end_line`/`new_text` args (identical on success and failure, and
  unaffected by drift relocation, which preserves the requested span's length); `detail_view()`
  caps `content` to 8 lines via `truncate_lines()`, same as `ReadFile`.
* `klorb.tools.replace_all.ReplaceAllTool` (`klorb/src/klorb/tools/replace_all.py`), name
  `ReplaceAll`. Replaces every occurrence of `search` in a single `filename` with `new_text`.
  `search` is matched as a literal substring by default; `is_regex` treats it as a Python
  regex, in which case `new_text` may use `\1`-style backreferences. `case_insensitive` and
  `multiline` (which maps to `re.MULTILINE`, only meaningful with `is_regex`) are both
  optional and default to `false`. The file is only rewritten if at least one replacement was
  made. The result is a dict: `filename`, `replacements_made` (the match count, returned as a
  blast-radius signal analogous to `EditFile`'s drift check), and `is_regex`. `summary()` names
  the file, the match count, and whether the match was literal or regex; no `detail_view()`
  override — the result is a few small scalars, so the default pretty-printed JSON is
  already a good fit.
* `klorb.tools.create_file.CreateFileTool` (`klorb/src/klorb/tools/create_file.py`), name
  `CreateFile`. Creates a new text file at `filename` with the given `content` (may be `""`),
  raising `FileExistsError` if the file already exists — file creation is always an explicit
  tool call, never an implicit side effect of `EditFile`. A full-file rewrite of an existing
  file goes through `EditFile` with `start_line=1, end_line=total_lines` instead. Missing
  parent directories are created automatically. The result is a dict: `filename`,
  `total_lines`, and `created: true`. `summary()` names the file and its line count; no
  `detail_view()` override, same reasoning as `ReplaceAll`.
* `klorb.tools.grep.GrepTool` (`klorb/src/klorb/tools/grep.py`), name `Grep`. Recursively
  searches the directory tree rooted at `dirname` (`""` means the whole project root) for lines
  matching `pattern` — a literal substring by default, or a Python regular expression when
  `is_regex` is true (an invalid regex raises `ValueError`). `case_insensitive` and the optional
  `file_glob` (matched against each file's bare name, e.g. `"*.py"`) narrow the search further.
  Walks via `klorb.tools.dir_walk.walk_readable_tree()` (see "Recursive tree walks" below) rather
  than a single `resolve_and_evaluate_read()` call, since the search spans however many
  directories the tree actually has. A file that fails to decode as UTF-8 (or fails to open at
  all) is skipped silently, matching common `grep -I` behavior. At most
  `context.process_config.grep_max_results` matches (default
  `process_config.DEFAULT_GREP_MAX_RESULTS`, 500) are returned per call. The result is a dict:
  `root` (the resolved search root), `pattern`, `is_regex`, `case_insensitive`, `file_glob`,
  `matches` (a list of `{filename, line_number, line}`), and `truncated`. `summary()` names the
  pattern, root, and match count; `detail_view()` caps `matches` to its first 20 entries (adding
  a `matches_omitted` count), since a full result can hold up to `grep_max_results` matches.
* `klorb.tools.find_file.FindFileTool` (`klorb/src/klorb/tools/find_file.py`), name `FindFile`.
  Recursively searches the directory tree rooted at `dirname` (`""` means the whole project
  root) for files whose bare name matches a glob `pattern` (e.g. `"*.py"` or `"*_context*"`;
  `case_insensitive` folds case on both sides of the match). Uses the same
  `walk_readable_tree()` walk as `Grep`. At most `context.process_config.find_file_max_results`
  matches (default `process_config.DEFAULT_FIND_FILE_MAX_RESULTS`, 500) are returned per call.
  The result is a dict: `root`, `pattern`, `case_insensitive`, `matches` (a list of absolute
  file paths), and `truncated`. `summary()` names the pattern, root, and match count;
  `detail_view()` caps `matches` the same way `Grep`'s does.

## Recursive tree walks

`Grep` and `FindFile` both need to walk a whole directory tree rather than resolve one path, so
the permission-aware traversal lives once in `klorb.tools.dir_walk.walk_readable_tree(context,
dirname)` rather than being duplicated between them. It resolves and checks `dirname` itself
exactly like `ListDir`'s `dirname` (`resolve_and_evaluate_read()`, raising
`PermissionError`/`PermissionAskRequired` if not `"allow"`), then yields
`(dir_path, subdir_names, file_names)` depth-first for that root and every directory beneath it
that `readDirs` permits — `dir_path` absolute and canonicalized, `subdir_names`/`file_names` bare
names sorted alphabetically. Every subdirectory encountered during the walk (not just the root)
gets its own `resolve_and_evaluate_read()` check before being descended into: one that isn't
`"allow"` is pruned — excluded from `subdir_names`, never yielded itself, never raising — rather
than aborting the whole walk, so one restricted subtree doesn't make a bulk search fail entirely.
See [the pruning ADR](../adrs/prune-non-allow-subdirs-during-recursive-tree-walk.md). A
subdirectory that is itself a symlink is also excluded and never descended into, regardless of
its own verdict, mirroring `os.walk`'s `followlinks=False` default so a symlink cycle can't
recurse forever — see
[the symlink ADR](../adrs/recursive-tree-walk-does-not-follow-symlinked-dirs.md). A symlinked
*file* (not a directory) is still listed normally in `file_names`, since it can't introduce a
cycle.

## Path safety

`EditFile`, `ReplaceAll`, and `CreateFile` all resolve their `filename` argument through
`klorb.permissions.workspace.resolve_within_workspace` before touching the filesystem, then
check the resolved path against `writeDirs` (`evaluate_write()`); `ReadFile` resolves and
checks via `resolve_and_evaluate_read()` in the same module, as does `ListDir`'s `dirname` and
`Grep`/`FindFile`'s `dirname` (the latter two also re-checking every subdirectory the walk
descends into — see "Recursive tree walks" above). See docs/specs/permissions.md for
the full permission-table design (allow/ask/deny rules, workspace-root confinement, and the
`ProcessConfig.workspace.trusted` distinction between `ReadFile` and the write tools) — this
spec no longer duplicates those details, which superseded the placeholder described in
[the workspace-root ADR](../adrs/confine-file-tools-to-workspace-root.md).

## Out of scope

* Recursive discovery into subpackages of `klorb.tools` is not implemented; tools are
  expected to live as flat modules directly under `klorb/src/klorb/tools/`.
