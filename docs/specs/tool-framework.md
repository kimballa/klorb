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
* `ToolSetupContext.session: Session | None` is the active `Session` itself (`None` for a
  `ToolSetupContext` built without a real `Session`, e.g. most unit tests), so a `Tool` can read
  and write `session.tool_state: dict[str, Any]` — a per-session, per-tool-name (keyed by
  `tool_state["<ToolName>"]`) scratch dict for ad hoc runtime bookkeeping a `Tool` wants to keep
  across calls within one session (e.g. `BashTool`'s one-time sandbox-fallback notice — see
  docs/specs/bash-tool-and-command-permissions.md's "Sandboxing" section), distinct from
  `session_config` (user-configurable settings only) and never persisted to disk. `Session`
  itself never reads or writes it — only the `Tool` that owns a given key does, via
  `session.tool_state.setdefault("<ToolName>", {})` (never assuming the key is pre-populated,
  since the dict starts empty for every new `Session`). `ToolSetupContext.session` is set on
  `ToolRegistry` post-construction by `Session.__init__` (`ToolRegistry` is always built before
  the `Session` it's passed into, so this can't be a `ToolRegistry` constructor argument) and
  threaded into every `ToolSetupContext` `ToolRegistry` builds from then on.
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
  * `aliases() -> Sequence[str] | None` — alternative names this tool can be invoked by.
    Defaults to `None`. Aliases are not advertised to the model in tool definitions, but if
    the model guesses one, `ToolRegistry.instantiate_tool` resolves it to the canonical tool
    as if called by `name()`. Currently assigned: `CreateFile` accepts `WriteFile`,
    `CreateMemory` accepts `WriteMemory`, `FindFile` accepts `Glob`.

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

  Two more are concrete and default to `None`, for a UI to render richer than plain text when a
  call has something more specific to show (see [[terminal-repl]]'s "Diff and read previews"):
  * `diff_preview(args, result=None, error=None) -> DiffPreview | None` — a `label` plus the
    `DiffHunk`s parsed back from `result["diff"]` (see
    `klorb.tools.util.diff_lines.build_diff_hunks()`), for a call whose result carries a
    structured diff. Overridden by `EditFile`/`CreateFile` and their `EditMemory`/
    `CreateMemory`/`EditScratchpad` counterparts; `None` on failure, same discriminant as
    `summary()`.
  * `read_preview(args, result=None, error=None) -> ReadPreview | None` — a `label`, up to 4
    numbered `preview_lines` from the read's own captured content, a `truncated` flag, and a
    lazy `open_full()` closure performing a fresh, passive re-read of the whole subject (no
    permission re-ask) only when a UI actually invokes it. Overridden by `ReadFile`/
    `ReadMemory`/`ReadScratchpad`/`ReadSkillFile`; `None` on failure.

  `default_tool_call_summary()`/`default_tool_call_detail()` (both in `klorb/src/klorb/tools/
  tool.py`) are also what a consumer falls back to for a tool call whose name isn't recognized
  by a `ToolRegistry` (so there's no `Tool` instance to call `.summary()`/`.detail_view()` on)
  — one implementation of the default rendering, not duplicated between the base class and
  that fallback path. See
  [the raw-callback-data ADR](../adrs/render-tool-calls-via-raw-callback-data.md) for how a
  `Session`-reported tool call actually reaches these methods.
* `klorb.tools.registry.ToolRegistry` (`klorb/src/klorb/tools/registry.py`) holds a
  name-keyed set of `Tool` subclasses and is the factory for them. `process_config`/
  `session_config` are held by reference, not copied, so later changes to either (e.g. a TUI
  command palette mutating `session_config` in place) are picked up by tools instantiated
  afterward. A registry is built in one of two ways:
  * `ToolRegistry.discover_tools(process_config, session_config, package=klorb.tools)` —
    the bootstrap classmethod that walks `package`'s modules once with
    `pkgutil.walk_packages`, imports each, and collects every concrete (non-abstract)
    `Tool` subclass defined directly in that module, returning a registry holding them all.
    By default it scans the `klorb.tools` package itself, so dropping a new module
    containing a `Tool` subclass into `klorb/src/klorb/tools/` is enough to register it —
    no manual registration step is required. A different package can be passed (used by
    tests to scan a fixture package, and by evals to scan an eval-tools package). The
    package scan runs only here, not in `__init__`, so the import/scan work isn't repeated
    when a session-scoped registry is built from an already-discovered class dict.
  * `ToolRegistry(process_config, session_config, tool_classes: dict[str, type[Tool]])` —
    constructs a registry directly from an already-discovered class dict, which it clones
    (not held by reference), so a session-scoped registry can be built from a subset of a
    bootstrap registry's classes without re-scanning any package. This is the construction
    path a restricted-tool subagent will use; the harness's own sessions today use
    `discover_tools` to get the full set.
  * `instantiate_tool(name: str) -> Tool` — the factory method: builds a fresh
    `ToolSetupContext` from the registry's current `process_config`/`session_config` and
    constructs a brand new instance of the named tool's class, raising `KeyError` if no tool
    with that name (or alias) was discovered. If `name` is not a canonical tool name, it is
    checked against every registered tool's `aliases()` before raising. Called once per
    requested tool call by `Session._run_tool_calls` (see [[session-and-turns]]), so a tool
    never carries state over between calls. See
    [the fresh-instance-per-call ADR](../adrs/tool-registry-instantiates-a-fresh-tool-per-call.md).
  * `tools() -> list[Tool]` — a freshly-instantiated `Tool` for every discovered tool.
  * `tool_definitions() -> list[dict[str, Any]]` — builds the OpenAI/OpenRouter
    function-calling `tools` array: each entry is
    `{"type": "function", "function": {"name", "description", "parameters"}}`, with
    pydantic parameter schemas converted to JSON schema via `model_json_schema()`.

## Malformed tool-call arguments

A model-generated tool call's `arguments` string sometimes isn't valid JSON at all — a total
parse failure, before any `Tool.apply()` (or any tool-specific argument validation) ever runs.
`Session._run_tool_calls()` (`klorb/src/klorb/session.py`) catches `json.JSONDecodeError` and
reports it back as that call's `tool_response` — `args={}` (no tool runs), `call.arguments`
blanked to `"{}"` so the malformed string isn't replayed to the API on a later turn — rather
than propagating out and aborting the whole turn.

The `tool_response` text (and the UI's `default_invalid_tool_call_detail()` rendering, via
`ToolCallEvent.raw_arguments`) both come from one shared, tool-agnostic helper,
`klorb.tools.tool.describe_tool_arg_json_error(name, raw_arguments, json_exc)`:

* **Offset framing** — `json_exc.lineno`/`colno`/`pos`/`msg` named explicitly, plus the raw
  string quoted for ~40 characters on each side of the break point with a caret line marking
  the exact position, rather than a bare character count the model has to count out itself.
* **XML detection** — if the first non-whitespace character of the raw string is `<`, the
  response short-circuits to a message stating tool-call arguments must be a JSON object (with
  a short correct-shape example), skipping the generic syntax-error teaching below.
* **Common JSON mistakes** — a fixed, multi-line primer (unescaped inner quotes, unbalanced
  brackets, comma problems, mismatched quotes), each as a bad → good contrast.
* **Edit-argument escaping hint** — gated on whether the raw string contains the literal
  substring `old_text`, `old_text_start`, `old_text_end`, or `new_text`: since the edit tools are the
  biggest producers of large, heavily-escaped string arguments, a call that mentions one of
  those names gets an extra, targeted reminder to double-check quoting/escaping in that field
  specifically. This is the only tool-aware piece of the helper; a malformed call to any other
  tool gets the offset framing and common-mistakes primer only.

`statistics.malformed_tool_calls` accounting is unaffected by any of this — it still increments
once per `JSONDecodeError` regardless of which message variant was produced.

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
  when more content exists past `end_line`), `content` — a single string with one
  `"N|line text"` entry per line, newline-separated — and, only when `truncated` is true,
  `next_start_line` (`end_line + 1`): the `start_line` to pass on the next call to keep paging
  through the file, so a caller doesn't have to compute it itself. `summary()` names the file
  and the returned line range; `detail_view()` caps `content` to 8 lines via `truncate_lines()`,
  since a full result can be up to `read_file_max_lines` (200 by default) lines.
* `klorb.tools.edit_file.EditFileTool` (`klorb/src/klorb/tools/edit_file.py`), name
  `EditFile`. Replaces a block of an existing text file's current content with `new_text`,
  locating that block by an exact text match rather than a line number — no `start_line`/
  `end_line`/line-number argument exists anywhere in this tool's schema; the model never does
  line arithmetic. Two mutually exclusive forms locate the block:
  * `old_text` alone — the entire replacement block, verbatim, as one string (one or more
    complete lines; never a sub-line fragment, since matching is always against whole file
    lines). Must match exactly one location in the subject.
  * `old_text_start`/`old_text_end` together — each must itself match exactly one location;
    everything from `old_text_start`'s match through `old_text_end`'s match, inclusive, is
    replaced by `new_text`. Use this instead of `old_text` for a longer span, so the untouched
    interior doesn't have to be repeated.

  Matching is always exact-first, falling back to a whitespace/punctuation-tolerant comparison
  (leading/trailing whitespace ignored; em/en dash and minus sign folded to a plain hyphen,
  curly double/single quotes folded to their straight equivalents) only when the exact search
  finds nothing — and only honored if that fallback resolves to exactly one location. A
  successful fallback match sets `fuzzy_whitespace_match: true` in the result, with a
  `whitespace` string describing what was tolerated.

  No match at all raises `ValueError` naming the anchor that failed and pointing at
  `reread_hint` (e.g. "re-ReadFile foo.py"), signaling stale content (e.g. from an earlier edit
  in the same turn) rather than corrupting the file. More than one match raises a
  distinctly-worded `"Ambiguous match"` `ValueError` listing ready-to-use candidate JSON
  fragments — one per matching location — each extending the anchor(s) with more surrounding
  context (grown outward, as many lines as needed, until every candidate is uniquely
  distinguishable — see `_minimal_disambiguating_n`) and recapitulating that same extra context,
  unchanged, in the candidate's own `new_text`, so a model can retry by copying one candidate
  verbatim rather than reconstruct it from a separate `ReadFile` call. There is no separate
  `context_before`/`context_after` argument, and no bounded search radius: every match search
  spans the whole subject.

  There is no separate insert or delete tool: insert without deleting by folding the anchor
  line's original text into `new_text` alongside the new content; delete by passing an empty
  `new_text`. The one exception is a missing or empty file (`total_lines == 0`), which has no
  content to anchor on — the only valid call there is `old_text=""`, which also covers a
  `filename` that doesn't exist yet at all: a missing file is treated exactly like an
  existing-but-empty one, `EditFileCore.apply()` creates it (and any missing parent
  directories, mirroring `CreateFileTool`) instead of raising, and the result gains
  `created: true`. Any other shape against a nonexistent file raises `FileNotFoundError` naming
  `CreateFile` (or `CreateMemory`, for `EditMemory`) as the tool to create it with first, rather
  than the bare OS `[Errno 2]` text. `EditMemoryTool` supports the same auto-create (see
  docs/specs/memories.md); `EditScratchpadTool` never hits this path, since the scratchpad file
  is harness-managed and always exists.

  Trailing-newline handling: an edit that doesn't touch the file's last line preserves whatever
  trailing-newline state the file already had; an edit that reaches the end of the file
  (including the empty-file case) always terminates the file with a single trailing `\n` if any
  content remains, none otherwise. The result is a dict: `filename`, the edited region's
  `start_line`/`end_line` (1-indexed, renumbered to reflect `new_text`'s own line count),
  `replaced_lines` (the line count of the block that was matched and replaced — possibly
  different from `end_line - start_line + 1`, since `end_line` reflects `new_text`'s length,
  not the original match's), the file's new `new_total_lines`, and `content` — the changed
  region in `ReadFile`'s `"N|text"` format, so the model can see the result without a follow-up
  `ReadFile` call. `summary()` reports a `"+A/-R"` line-diff count computed from `replaced_lines`
  and `new_text`'s own line count — available only on success, since a failed match never
  resolves a location to count lines removed from; `detail_view()` caps `content` to 8 lines via
  `truncate_lines()`, same as `ReadFile`.

  `EditFileCore.apply()` (`klorb/src/klorb/tools/util/edit_file_core.py` — the mechanic
  `EditFileTool`, `EditScratchpadTool`, and `EditMemoryTool` all delegate to, see
  [[read-edit-file-scratchpad-share-core-via-composition]]) rejects any other argument
  combination (`old_text` alongside a meaningful `old_text_start`/`old_text_end`, only one of
  `old_text_start`/`old_text_end`, neither form present) with a specific `ValueError` naming the
  problem. `new_text` is the only field every edit tool's schema lists as `required` (plus
  `filename`/`namespace` where applicable) — `old_text`/`old_text_start`/`old_text_end` aren't,
  since the accepted combinations are cross-field rules `_normalize_edit_args()` enforces, not
  something a JSON-schema `anyOf`/`oneOf` can express cleanly.
* `klorb.tools.replace_all.ReplaceAllTool` (`klorb/src/klorb/tools/replace_all.py`), name
  `ReplaceAll`. Replaces every occurrence of `search` in a single `filename` with `new_text`.
  `search` is matched as a literal substring by default; `is_regex` treats it as a Python
  regex, in which case `new_text` may use `\1`-style backreferences. `case_insensitive` and
  `multiline` (which maps to `re.MULTILINE`, only meaningful with `is_regex`) are both
  optional and default to `false`. The file is only rewritten if at least one replacement was
  made. The result is a dict: `filename`, `replacements_made` (the match count, returned as a
  blast-radius signal analogous to `EditFile`'s ambiguous-match check), and `is_regex`. `summary()` names
  the file, the match count, and whether the match was literal or regex; no `detail_view()`
  override — the result is a few small scalars, so the default pretty-printed JSON is
  already a good fit.
* `klorb.tools.create_file.CreateFileTool` (`klorb/src/klorb/tools/create_file.py`), name
  `CreateFile`. Creates a new text file at `filename` with the given `content` (may be `""`),
  raising `FileExistsError` if the file already exists — file creation is always an explicit
  tool call, never an implicit side effect of `EditFile`. A full-file rewrite of an existing
  file goes through `EditFile` with `old_text` set to the file's entire current content
  instead. Missing
  parent directories are created automatically. The result is a dict: `filename`,
  `total_lines`, and `created: true`. `summary()` names the file and its line count; no
  `detail_view()` override, same reasoning as `ReplaceAll`.
* `klorb.tools.grep.GrepTool` (`klorb/src/klorb/tools/grep.py`), name `Grep`. Recursively
  searches the directory tree rooted at `dirname` (`""` means the whole project root) for lines
  matching any of `queries` — each matched as a literal substring under `search_mode` `"literal"`
  (the default) or `"semantic"`, or as a distinct Python regular expression when `search_mode` is
  `"regex"` (an invalid regex raises `ValueError`); a line matching any one query counts as a hit,
  equivalent to `grep -e query1 -e query2 ...`. `search_mode="semantic"` additionally merges in up
  to `top_k` (default 10) chunk-level hits from the workspace's local hybrid search index (see
  docs/specs/local-search-index.md), scoped by the same `dirname`/`file_glob` — a file a semantic
  hit contributed to carries `match_kind` (`"literal"`/`"semantic"`/`"literal+semantic"`) and
  `score`; raises `ToolCallError` if the index isn't available. See
  docs/adrs/00193-grep-search-mode-adds-semantic-hits-via-workspace-index.md.
  `case_insensitive` and the optional `file_glob` (matched against each file's bare name, e.g.
  `"*.py"`) narrow the search further. Walks via `klorb.tools.util.walk_readable_tree()`
  (see "Recursive tree walks" below) rather than a single `resolve_and_evaluate_read()` call,
  since the search spans however many directories the tree actually has. A file that fails to
  decode as UTF-8 (or fails to open at all) is skipped silently, matching common `grep -I`
  behavior. Each hit is reported with `context.process_config.grep_context_lines` (default
  `process_config.DEFAULT_GREP_CONTEXT_LINES`, 2) lines of surrounding context on each side, like
  `grep -C`; overlapping or adjacent context windows within the same file are merged rather than
  reported as separately-overlapping results. At most
  `context.process_config.grep_max_results` matching lines (default
  `process_config.DEFAULT_GREP_MAX_RESULTS`, 500) are returned per call. The result is a dict:
  `root` (the resolved search root), `queries`, `search_mode`, `case_insensitive`, `file_glob`,
  `context_lines`, `files` (a list of `{filename, lines}`, one entry per matching file), `match_count`,
  and `truncated`. Each `lines` entry is a compact dense-format string — `"*42|matched text"` or
  `" 41|context text"`, a leading `*`/space match marker, the 1-based line number, a `|`, and the
  line's text — built by the shared `klorb.tools.util.search_core` helpers; because every line
  carries its own number, a file's merged context windows are concatenated into one flat `lines`
  list with no `start_line`/`end_line` wrapper, and a break between windows shows up only as a jump
  in the embedded line numbers (see the ADR
  `grep-search-tools-share-dense-line-core.md`). A line longer than
  `context.process_config.grep_max_line_length` (default
  `process_config.DEFAULT_GREP_MAX_LINE_LENGTH`, 500) characters is truncated with a trailing
  `"[truncated...]"` suffix, guarding against a single pathologically long line (a minified
  sourcemap, a one-line JSON blob) dumping an outsized chunk of text into the model's context.
  If the JSON serialization of `files` would exceed `context.process_config.grep_spill_bytes`
  (default `process_config.DEFAULT_GREP_SPILL_BYTES`, 32768) bytes, `GrepTool` writes it to a
  file in this session's spill tmpdir instead and reports `results_data_file` (the file's path)
  in place of `files`, via `klorb.tools.util.spill.SpillDir` — the same session-scoped tmpdir
  mechanism `WebFetchTool` (`klorb.tools.web.spill`) uses for its own oversized response bodies,
  so a session calling either tool repeatedly reuses one tmpdir instead of accumulating one per
  call; the tmpdir is granted `readDirs` access the same way `BashTool` grants its own spilled
  `stdout`/`stderr` directories (see docs/specs/bash-tool-and-command-permissions.md's
  "stdout/stderr capture" section). `GrepTool` must be constructed with a live `Session` for a
  spill to succeed — a `ToolSetupContext` without one raises `ToolCallError` if a spill is
  needed. `summary()` names the queries,
  root, and match count; `detail_view()` caps `files` to its first 20 entries (adding a
  `files_omitted` count) when present, since a full result can span up to `grep_max_results`
  matching lines across that many files.
* `klorb.tools.find_file.FindFileTool` (`klorb/src/klorb/tools/find_file.py`), name `FindFile`.
  Recursively searches the directory tree rooted at `dirname` (optional; omitted or `""` means the whole project
  root) for files and directories whose bare name matches a glob `pattern` (e.g. `"*.py"` or
  `"*_context*"`; `case_insensitive` folds case on both sides of the match) — mirroring `find
  -name`'s default of matching every node type, not just files (see
  `docs/adrs/00170-findfile-matches-directory-names-not-just-files.md`). Uses the same
  `walk_readable_tree()` walk as `Grep`. At most `context.process_config.find_file_max_results`
  matches (default `process_config.DEFAULT_FIND_FILE_MAX_RESULTS`, 500) are returned per call.
  The result is a dict: `root`, `pattern`, `case_insensitive`, `matches` (a list of
  `{"file": path}` or `{"dir": path}` entries — a directory match doesn't stop the walk from
  descending into it), and `truncated`. `summary()` names the pattern, root, and match count;
  `detail_view()` caps `matches` the same way `Grep`'s does.

## Recursive tree walks

`Grep` and `FindFile` both need to walk a whole directory tree rather than resolve one path, so
the permission-aware traversal lives once in `klorb.tools.util.walk_readable_tree(context,
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
`SessionConfig.workspace.trusted` distinction between `ReadFile` and the write tools) — this
spec no longer duplicates those details, which superseded the placeholder described in
[the workspace-root ADR](../adrs/confine-file-tools-to-workspace-root.md).

## Out of scope

* Recursive discovery into subpackages of `klorb.tools` is not implemented; tools are
  expected to live as flat modules directly under `klorb/src/klorb/tools/`.
