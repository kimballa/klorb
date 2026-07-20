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
    with that name was discovered. Called once per requested tool call by
    `Session._run_tool_calls` (see [[session-and-turns]]), so a tool never carries state over
    between calls. See
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
  substring `start_text`, `end_text`, `old_text`, or `new_text`: since the edit tools are the
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
  `EditFile`. Replaces the inclusive 1-indexed line range `start_line`..`end_line` of an
  existing text file with `new_text`, after locating an anchor (`start_text`/`end_text`, or the
  whole-block `old_text` form described below) at or near those lines. When a real line hint is
  given (not `old_text`'s hint-less `unbounded` search mode — see below) and the anchor already
  matches exactly at that hint, with no `context_before`/`context_after` supplied, the edit
  applies immediately — no scan for other nearby candidates at all, even if the same content
  also occurs elsewhere within the drift radius; a caller who names a specific, correct location
  doesn't need it cross-checked against lookalikes. See
  [the exact-hint-match ADR](../adrs/edit-file-exact-hint-match-skips-ambiguity-scan.md) for why,
  and for the deliberate tradeoff this makes against the drift-tolerance ADR's original
  "never silently edit the wrong location" guarantee. Otherwise, `start_line`/`end_line` are a
  location hint, not a hard requirement: if the anchor doesn't match exactly, `apply()`
  searches within `context.process_config.edit_file_drift_search_radius` lines (default
  `process_config.DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS`, 20 — the sole canonical source of
  this default) for a unique nearby location where it still matches at the same relative span,
  and edits there instead, reporting the correction back (see the response shape below). No
  match within that radius still raises `ValueError` naming the actual content at the hint,
  signaling stale line numbers (e.g. from an earlier edit shifting everything below it) rather
  than corrupting the file; more than one match raises a distinctly-worded `"Ambiguous match"`
  `ValueError` listing the candidate lines along with each one's actual nearby content as a
  ready-to-use `context_before=...`/`context_after=...` value, adaptively as many lines on each
  side as needed to tell every candidate apart (fewer near a file's start/end, and no fixed cap
  — see `_minimal_disambiguating_window`), resolvable by retrying with a closer `start_line` or
  the optional `context_before`/`context_after` arguments (checked against every candidate
  whenever supplied) — the preview lets a model copy that value verbatim for the location it
  means rather than reconstruct it from a separate `ReadFile` call. Omitting
  `context_before`/`context_after` means "don't check this side"; passing the empty string `""`
  instead is a distinct, checked assertion that there's genuinely nothing on that side (the
  target is the file's actual first/last line) — but a model reliably fumbles sending a
  genuinely empty string (often as bare, unquoted whitespace in the tool-call JSON, producing a
  parse error rather than the intended call), so `context_before_start`/`context_after_end`
  (booleans) are offered as an easier-to-send equivalent: `context_before_start=true` with no
  `context_before` behaves exactly like `context_before=""`, and likewise for
  `context_after_end`/`context_after`. `_normalize_edit_args()` only consults the boolean when
  the corresponding string argument is entirely absent — an explicit `context_before`/
  `context_after` (even `""`) always wins. Both mechanisms produce the identical downstream
  assertion; the boolean form exists purely because it's easier for a model to get right, not
  because it means something different. An out-of-bounds hint (`end_line` past the file's end,
  etc.) raises immediately with no search attempted. See
  [the drift-tolerance ADR](../adrs/edit-file-tolerates-bounded-line-drift-via-local-candidate-search.md).
  There is no separate insert or delete tool: insert without deleting by setting
  `start_line == end_line` and folding that line's original text into `new_text`; delete by
  passing an empty `new_text`. The one exception is an empty file (`total_lines == 0`), which
  has no anchor line to replace — the only valid call there is `start_line=1, end_line=0,
  start_text="", end_text=""`. See
  [the insert/delete ADR](../adrs/edit-file-covers-insert-and-delete-via-replace-range.md).
  That same empty-subject shape also covers a `filename` that doesn't exist yet at all: a
  missing file is treated exactly like an existing-but-empty one, `EditFileCore.apply()`
  creates it (and any missing parent directories, mirroring `CreateFileTool`) instead of
  raising, and the result gains `created: true`. Any other shape against a nonexistent file
  raises `FileNotFoundError` naming `CreateFile` (or `CreateMemory`, for `EditMemory`) as the
  tool to create it with first, rather than the bare OS `[Errno 2]` text — this mechanic can
  only ever create a *whole new* file, never edit a specific line range of one that isn't there
  yet. `EditMemoryTool` supports the same auto-create (see docs/specs/memories.md);
  `EditScratchpadTool` never hits this path, since the scratchpad file is harness-managed and
  always exists. See
  [the auto-create ADR](../adrs/edit-file-auto-creates-via-empty-subject-insert-shape.md).
  Trailing-newline handling: an edit that doesn't touch the file's last line preserves whatever
  trailing-newline state the file already had; an edit whose `end_line` reaches the end of the
  file (including the empty-file case) always terminates the file with a single trailing `\n`
  if any content remains, none otherwise. The result is a dict: `filename`,
  `requested_start_line`/`requested_end_line` (echoing the input — `requested_end_line` echoes
  an `end_line` *inferred* from `old_text` when the call didn't supply one, see below), the
  edited region's `start_line`/`end_line` (where the edit actually landed, renumbered to reflect
  what was actually written — possibly different from what was requested), `line_hint_matched`
  (false if a drift relocation happened), the file's new `new_total_lines`, and `content` — the
  changed region in `ReadFile`'s `"N|text"` format, so the model can see the result without a
  follow-up `ReadFile` call. `summary()` reports a `"+A/-R"` line-diff count computed from the
  call's own `start_line`/`end_line`/`new_text` args (identical on success and failure, and
  unaffected by drift relocation, which preserves the requested span's length); `detail_view()`
  caps `content` to 8 lines via `truncate_lines()`, same as `ReadFile`.

  **The accepted argument matrix.** `EditFileCore.apply()` (`klorb/src/klorb/tools/util/
  edit_file_core.py` — the mechanic `EditFileTool`, `EditScratchpadTool`, and `EditMemoryTool`
  all delegate to, see [[read-edit-file-scratchpad-share-core-via-composition]]) accepts several
  argument shapes, all resolved into the same concrete `start_line`/`end_line`/
  `start_text`/`end_text` tuple by a normalization step (`_normalize_edit_args()`) before any of
  the drift-search machinery above ever runs — that machinery stays almost unchanged regardless
  of which form a call used. "Line hint" below means `start_line`, or one of its aliases —
  `line`, `line_num`, `line_no`, or `line_number` (bare, no-description schema properties — see
  [the alias-schema ADR](../adrs/edit-file-hint-aliases-as-bare-schema-properties.md)) —
  accepted in *every* form, not just `old_text`, since the schema advertises them
  unconditionally; `start_line` and an alias with the *same* value is not a conflict, but
  differing values raise `ValueError`. Because an alias can always stand in for `start_line`,
  no edit tool's schema lists `start_line` itself as `required` either — see the required-list
  bullet below. Every form still requires `new_text` and a line hint:
  * *Classic* — `end_line`, `start_text`, `end_text` required (plus a line hint). Still the most
    token-efficient form for a long replacement span, since it never repeats the interior.
  * *Single-line shortcut* — when `start_line == end_line`, `end_text` may be omitted or empty;
    `start_text` alone anchors the one line being replaced. `end_line` must still be present and
    equal to `start_line` *unless it's omitted entirely too*: when both `end_text` and `end_line`
    are absent, `_normalize_edit_args()` imputes `end_line = start_line` and `end_text =
    start_text` — `start_text` unambiguously names the single line being replaced regardless of
    how many lines `new_text` spans, so a multi-line `new_text` in this shape is an ordinary
    insert (the one line growing into several), not an error.
  * *`old_text`* — instead of `start_text`/`end_text`, the caller supplies `old_text`: the
    entire contiguous replacement block, verbatim, as one multi-line string. `end_line` is
    optional, inferred by counting `old_text`'s lines; if supplied, it must agree with that
    count or `_normalize_edit_args()` raises, naming both counts. A 1-line `old_text` is a
    natural instance of this form and converges in
    effect (not argument shape) with the single-line shortcut. `old_text` is verified against
    the file *in full* — every line of the candidate span, not just its first and last — a
    strict superset of the classic form's endpoints-only check; see
    [the full-block-verification ADR](../adrs/edit-file-old-text-verifies-full-block.md). A
    zero-candidate match names the first interior line that actually differs, not just
    "start/end didn't match." The line hint itself is optional in this form too: when `old_text`
    is given with no `start_line` and no alias, the drift search scans the *entire* subject for a
    unique match instead of a window within `edit_file_drift_search_radius` lines of a hint —
    `requested_start_line`/`requested_end_line` then echo the `1`/`len(old_text)`-derived seed
    the search used rather than a real caller-supplied hint, and `line_hint_matched` is always
    `False` in this mode (there was no hint to match). Supplying `end_line` with no line hint is
    an error (`end_line` needs something to be relative to); omit both to search unbounded, or
    supply `start_line` (or an alias) to bound the search as usual.
  * *Implicit `start_text` → `old_text` conversion* — a multi-line `start_text` with `end_text`
    omitted or empty is reinterpreted as `old_text` (so pasting the whole block into
    `start_text` still produces a useful edit instead of the classic form's lossy
    truncate-to-first-line fallback below), *only* when `end_text` is absent — see
    [the form-6-precedence ADR](../adrs/edit-file-form6-only-converts-without-end-text.md). A
    multi-line `start_text` *with* a non-empty `end_text` keeps the pre-existing behavior:
    `start_text` is truncated to its first line (with an advisory `feedback` note) and
    `end_text` anchors the end.
  * Every edit tool's `required` schema list is relaxed to just the fields every form needs
    (`new_text`, plus `filename`/`namespace` where applicable) — `start_line` itself is never
    `required` either, since an alias can always substitute for it. The cross-field rules
    distinguishing accepted from rejected combinations live entirely in
    `_normalize_edit_args()`, not in the JSON schema (no `anyOf`/`oneOf`); see
    [the required-relaxed ADR](../adrs/edit-file-required-relaxed-not-anyof.md). A rejected
    combination (`old_text` alongside a meaningful `start_text`/`end_text`, neither `old_text`
    nor `start_text` present, no line hint in any spelling outside `old_text` mode, `end_line`
    given with no line hint, etc.) raises a specific `ValueError` naming the problem.
  * The legacy empty-subject insert form (`start_line=1, end_line=0, start_text=""`, `end_text`
    either omitted or `""`) is untouched and not re-expressed in `old_text` terms — there's no
    block to anchor. Because that pair would otherwise route through the single-line-shortcut
    path (which requires `end_line == start_line`), `_normalize_edit_args()` carries an explicit
    carve-out keyed on the exact `start_line=1, end_line=0` pair (not merely on `start_text`/
    `end_text` being empty, which would let an unrelated multi-line call with blank anchors at
    both endpoints skip anchor verification entirely): when `start_line == 1 and end_line == 0
    and start_text == ""`, the shortcut's equality check is skipped so this legacy call still
    reaches `_resolve_line_range_edit()`'s own validation.
  * `apply()`'s `feedback` list gains two advisory-only entries beyond the pre-existing
    multi-line-truncation note: one when the implicit `start_text` → `old_text` conversion fired
    (naming `old_text` as the more direct spelling for next time), and one when the line hint
    came from an alias rather than `start_line` (naming which alias was accepted). A clean use
    of any other first-class form gets no feedback, so a supported idiom isn't nudged as if it
    were off-pattern.
  * The prose teaching an agent *when* to prefer each form (the decision rule, and worked
    examples for the long-span, `old_text`, and single-line-shortcut cases) lives in the system
    prompt's "Editing files" section (`klorb/src/klorb/resources/system_prompts.d/
    default_sys.md`), not in `EditFileCore.parameter_properties()`'s per-argument descriptions —
    that schema is inlined into every edit tool's definition and paid for on every turn, so it
    stays terse.
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
  matching any of `queries` — each matched as a literal substring by default, or as a distinct
  Python regular expression when `is_regex` is true (an invalid regex raises `ValueError`); a
  line matching any one query counts as a hit, equivalent to `grep -e query1 -e query2 ...`.
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
  `root` (the resolved search root), `queries`, `is_regex`, `case_insensitive`, `file_glob`,
  `context_lines`, `files` (a list of `{filename, lines}`, one entry per matching file), `match_count`,
  and `truncated`. Each `lines` entry is a compact dense-format string — `"*42|matched text"` or
  `" 41|context text"`, a leading `*`/space match marker, the 1-based line number, a `|`, and the
  line's text — built by the shared `klorb.tools.util.search_core` helpers; because every line
  carries its own number, a file's merged context windows are concatenated into one flat `lines`
  list with no `start_line`/`end_line` wrapper, and a break between windows shows up only as a jump
  in the embedded line numbers (see the ADR
  `grep-search-tools-share-dense-line-core.md`). `summary()` names the queries, root, and match
  count; `detail_view()` caps `files` to its first 20 entries (adding a `files_omitted` count),
  since a full result can span up to `grep_max_results` matching lines across that many files.
* `klorb.tools.find_file.FindFileTool` (`klorb/src/klorb/tools/find_file.py`), name `FindFile`.
  Recursively searches the directory tree rooted at `dirname` (optional; omitted or `""` means the whole project
  root) for files whose bare name matches a glob `pattern` (e.g. `"*.py"` or `"*_context*"`;
  `case_insensitive` folds case on both sides of the match). Uses the same
  `walk_readable_tree()` walk as `Grep`. At most `context.process_config.find_file_max_results`
  matches (default `process_config.DEFAULT_FIND_FILE_MAX_RESULTS`, 500) are returned per call.
  The result is a dict: `root`, `pattern`, `case_insensitive`, `matches` (a list of absolute
  file paths), and `truncated`. `summary()` names the pattern, root, and match count;
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
