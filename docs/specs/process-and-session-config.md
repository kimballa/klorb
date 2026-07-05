# Process and session configuration

## Summary

klorb has two layers of configuration: `SessionConfig` (`klorb/src/klorb/session.py`),
scoped to one `Session`'s lifetime, and `ProcessConfig` (`klorb/src/klorb/process_config.py`),
scoped to the whole process's lifetime. Today one process runs one session at a time; the
split exists so that, once klorb runs several sessions concurrently, settings that must stay
identical across all of them (a UI limit, a permission a session can't override) have a
single home, while each session still gets its own independent, mutable config. See
[the composition ADR](../adrs/nest-sessionconfig-inside-a-process-scoped-processconfig.md)
for why `ProcessConfig` nests a `session: SessionConfig` field rather than sharing one model
or slicing a superset.

## How it works

* `ProcessConfig` (`process_config.py`) has one nested field, `session: SessionConfig`, plus
  eight process-only fields today: `prompt_input_max_lines` (int, default `12` — the REPL
  prompt textarea's max grow height, applied via `input_widget.styles.max_height` in
  `ReplApp.on_mount()`), `thinking_token_budgets` (`dict[ThinkingEffort, int]`, default
  `session.THINKING_EFFORT_TOKEN_BUDGETS` — the token budgets `Session._reasoning_params()`
  looks `config.thinking_effort` up in for `"tokens"`-style models), `read_file_max_lines`
  (int, default `DEFAULT_READ_FILE_MAX_LINES`, `200` — `ReadFileTool`'s per-call page size;
  see "Out of scope" below), `openrouter_base_url` (str, default
  `openrouter.OPENROUTER_BASE_URL` — passed to `OpenRouterApiProvider(base_url=...)`, for
  pointing at a self-hosted or corporate OpenRouter-compatible gateway), `shell_command` (str,
  default `DEFAULT_SHELL_COMMAND`, `/bin/bash` — the shell binary a `!`-prefixed REPL command
  is run through; see [[terminal-repl]]), `shell_timeout_seconds` (`float | None`, default
  `None` — how long a `!`-prefixed REPL command may run before it's killed; `None` means no
  timeout), and `compatibility_claude_markdown` (bool, default `False` — when `True`,
  `CLAUDE.md` is read from the workspace root and injected into the conversation alongside
  `AGENTS.md`; see [[workspace-context-files]]).

  `SessionConfig` (`session.py`) additionally carries `max_tool_calls_per_turn` and
  `max_tool_calls_per_session` (int, defaults `session.DEFAULT_MAX_TOOL_CALLS_PER_TURN`/
  `DEFAULT_MAX_TOOL_CALLS_PER_SESSION` — `50`/`200`) — safety caps `Session._run_tool_calls()`
  enforces on individual tool-call dispatches. These live on `SessionConfig`, not
  `ProcessConfig`, specifically because reaching one can raise it: `Session` may ask (via an
  `on_tool_call_limit_reached` callback — see [[session-and-turns]]) whether to double it and
  keep going, mutating `self.config` for the rest of that session's lifetime — a per-session,
  interactively-mutable value, which is exactly what `SessionConfig` (not `ProcessConfig`,
  whose fields must stay identical across every concurrently running session) is for. See
  [the tool-call caps ADR](../adrs/cap-tool-calls-per-turn-and-per-session.md).

  `SessionConfig` also carries `read_dirs`/`write_dirs` (`DirRules`, default empty
  `deny`/`ask`/`allow` lists) — the `readDirs`/`writeDirs`-config-driven permission rules the
  file tools consult (see docs/specs/permissions.md). These live on `SessionConfig` for the
  same reason as the tool-call caps above: a future "ask" flow will let a user approve a rule
  for the rest of the session, a per-session mutation `ProcessConfig` can't represent.

  `SessionConfig.workspace` (a `klorb.workspace.Workspace`) is which directory this session
  considers its project root, whether it's a registered project, and whether it's trusted;
  `workspace.trusted` governs whether `ReadFile` gets a hard workspace-root boundary or
  table-only confinement (see docs/specs/permissions.md and docs/specs/projects-and-trust.md).
  It lives on `SessionConfig`, not `ProcessConfig`, for the same concurrency reason as
  `read_dirs`/`write_dirs`/the tool-call caps above — multiple sessions in one process could
  each be pointed at a different directory — see
  [the workspace-on-session ADR](../adrs/move-workspace-from-processconfig-to-sessionconfig.md).
  Deliberately has **no** on-disk key at all, in either `SESSION_KEY_MAP` or `PROCESS_KEY_MAP` —
  a project must never be able to grant itself trust via its own config file.

  `session` is a *template*, not a shared instance — every `Session` created in the process
  (at startup, or via `/clear`) gets `process_config.session.model_copy()`, an independent
  copy it can mutate freely without affecting any other session or the template itself. The
  process-only fields, by contrast, are read directly wherever they're needed (e.g.
  `self._process_config.prompt_input_max_lines`) rather than copied, since they must be
  identical across every concurrently running session.
* `load_process_config(*, config_flag_path: Path | None = None, cwd: Path | None = None,
  workspace: Workspace | None = None) -> ProcessConfig` assembles the config once at process
  startup by layering plain JSON objects, each read via
  [`read_versioned_json`](persisted-json-schema-versioning.md) (or, for the first layer,
  `klorb.schema_envelope.parse_versioned_json` directly on packaged resource text), in
  increasing order of precedence:
  1. The packaged built-in defaults, `klorb.resources/default-config.json`
     (`process_config._default_config_layer()`) — read via `importlib.resources`, shipped
     inside every klorb install, and therefore never missing, unlike every layer below. This
     is the file to edit when *shipping* a new default (see "On-disk key naming" below); it's
     never written to, copied, or user-editable.
  2. `/etc/klorb/klorb-config.json`, or the file named by `$KLORB_ETC_CONFIG` if that env
     var is set. Resolved lazily, at call time rather than import time, specifically so a
     value set via a `.env` file (loaded by `klorb.cli.main()`'s `load_dotenv()` call before
     `load_process_config()` runs) takes effect — unlike `KLORB_CONFIG_DIR` below, which
     (per [[paths-and-logging]]) is resolved once at `klorb.paths` import time and so does
     *not* currently pick up a `.env`-supplied value.
  3. `$KLORB_CONFIG_DIR/klorb-config.json` (defaults to `~/.config/klorb`; see
     [[paths-and-logging]] for the `KLORB_CONFIG_DIR` override). `klorb init` (see
     [[klorb-init]]) is what actually creates this file, from a *different* packaged
     resource — `klorb.resources/template-config.json` — a spartan starter meant for
     hand-editing, not the built-in-defaults file above.
  4. `cwd/.klorb/klorb-config.json`
  5. The file named by `--config` on the command line, if given.
  6. Overrides carried over from the previous session's saved state — a placeholder for now
     (`_load_last_session_overrides()` always returns `{}`), reserved for when
     `last-session.json` (see `TODO.md`) exists.

  Each layer is one JSON object shaped like `docs/specs/persisted-json-schema-versioning.md`
  describes, plus a `sessionDefaults` key holding a nested object of session-scoped
  settings — see "On-disk key naming" below. The top-level object and the `sessionDefaults`
  object are merged independently across layers (`dict.update` on each, not a deep merge),
  so a layer can override just one session default, or just one process-only setting,
  without repeating everything else. A missing file at layer 2 and below is silently skipped
  — that's the expected common case, not an error — and logged at debug level either way, so
  a user debugging "why didn't my config apply" can see what was and wasn't found. Layer 1
  is never missing, so nothing has to fall back to a bare pydantic field default in practice
  — see "Out of scope" below.

  One exception: `sessionDefaults.readDirs`/`writeDirs` are **concatenated** across layers
  instead of replaced — a layer's `deny`/`ask`/`allow` entries add to, rather than replace,
  every earlier layer's. See docs/specs/permissions.md for why (a stricter rule from any
  layer must never be discardable by a looser rule from another).

  `SessionConfig.workspace` is also set here, from a `Workspace` resolved via
  `klorb.workspace.TrustManager.resolve_workspace(cwd)` (or synthesized from
  `find_workspace_root(cwd)` — an ancestor search for the nearest directory with a
  non-symlinked `.klorb` child — when no `workspace` argument is given) — rather than
  `SessionConfig`'s own bare `Field(default_factory=lambda: Workspace(path=Path.cwd()))` (which
  remains only as the fallback for callers constructing `SessionConfig` directly, e.g. tests).
  See docs/specs/permissions.md and docs/specs/projects-and-trust.md.

  After merging, `_route_keys()` looks each on-disk key up in `SESSION_KEY_MAP` (for
  `sessionDefaults` keys) or `PROCESS_KEY_MAP` (for top-level keys) — both in
  `process_config.py` — to find which internal attribute it sets; a key with no entry in the
  relevant map is dropped with a warning logged (almost certainly a typo, not an
  intentionally-unrecognized setting).
* CLI flags (other than `--config` itself, which selects a layer above) are applied by
  `klorb.cli.main()` directly on top of the loaded `ProcessConfig`, since only `cli.py` knows
  the argparse `Namespace` shape (see `CLAUDE.md`'s CLI/library firewall rule). Today that's
  `--model`, whose argparse default is `None` (not `openai/gpt-5-nano`) specifically so
  `main()` can tell "flag not given" (leave whatever the config layers produced) apart from
  "flag explicitly given" (overwrite it) — see [the `--interactive` pattern in
  `add-cli-flag`](../../.claude/skills/add-cli-flag/SKILL.md) for why `None` defaults matter
  here.
* Four settings are user-editable at the session level via the TUI's command palette
  (`ModelCommandProvider`, `ThinkingCommandProvider` in `klorb/src/klorb/tui/`) and, when
  changed, are written to *both* the live session's `SessionConfig` and
  `ReplApp._process_config.session`, so a later `/clear` (or, eventually, a new session
  started elsewhere in the same process) inherits the change: model (`select_model`),
  thinking enabled (`set_thinking_enabled`), and thinking effort (`set_thinking_effort`).
  Provider selection isn't implemented yet (see `TODO.md`'s `ProviderFactory` item); when it
  is, it should follow the same dual-write pattern.
* `ReplApp.clear_session()` (`klorb/src/klorb/tui/repl.py`) builds the new session's config
  as `self._process_config.session.model_copy()` — the *current* template, including any
  dual-written changes — rather than hand-picking individual fields. This is why `/clear`
  carries over thinking settings as well as the model: an earlier version only copied
  `model` and `interactive` explicitly and silently reset thinking settings to their
  `SessionConfig` defaults on every `/clear`.

## On-disk key naming

A `klorb-config.json` file has exactly one level of structural nesting: a top-level
`sessionDefaults` object holds every session-scoped setting — mirroring `ProcessConfig`
nesting `session: SessionConfig` internally — and everything else, process-only settings,
sit as flat keys alongside it at the top level:

```json
{
  "schema": {"name": "klorb-config", "version": "1.0.0"},

  "sessionDefaults": {
    "model": "openai/gpt-5-nano",
    "thinking.enabled": true,
    "thinking.effort": "high",
    "tools.maxCallsPerTurn": 50,
    "tools.maxCallsPerSession": 200,
    "readDirs": {"deny": ["/nope"], "ask": ["/home/aaron/maybe"], "allow": ["/yolo"]},
    "writeDirs": {"deny": [], "ask": [], "allow": []}
  },

  "thinking.tokenBudgets": {"low": 4096, "medium": 16384, "high": 32768},
  "terminal.input.maxLines": 12,
  "tools.readFile.maxLines": 200,
  "tools.grep.maxResults": 500,
  "tools.findFile.maxResults": 500,
  "providers.openrouter.baseUrl": "https://openrouter.ai/api/v1",
  "shell.command": "/bin/bash",
  "shell.timeout": null
}
```

There are, as of `readDirs`/`writeDirs`, three distinct cross-layer merge behaviors in one
file — a reader shouldn't assume there are only the first two:

1. **Scalar replace** (most keys, e.g. `model`, `terminal.input.maxLines`): a later layer's
   value replaces an earlier layer's outright.
2. **Wholesale object replace** (`thinking.tokenBudgets`): the whole nested object is replaced,
   not deep-merged — a layer overriding it must repeat every `ThinkingEffort` key it wants to
   keep.
3. **Array concatenate** (`readDirs`/`writeDirs`): each of `deny`/`ask`/`allow` is extended,
   not replaced, across every layer — see docs/specs/permissions.md.

Within each of those two objects, keys are flat strings with dot-delineated namespaces in
lowerCamelCase — e.g. `"thinking.tokenBudgets"`, `"terminal.input.maxLines"` (the key is the
literal string `"terminal.input.maxLines"`, not a path into nested JSON objects — the same
style VSCode and Claude Code use for their own settings files) — grouped by *feature area*,
not by scope. `thinking.enabled`/`thinking.effort` (inside `sessionDefaults`) and
`thinking.tokenBudgets` (top-level) share the `thinking.` namespace despite living in
different objects, because the namespace answers "what is this setting about," not "who
owns it." `model` has no natural feature-area grouping yet, so it stays a flat key inside
`sessionDefaults`, matching how VSCode itself mixes ungrouped and namespaced keys in one
settings file. `interactive` is *not* a recognized key anywhere in the file — see
"Configuration" below.

This is deliberately a different naming convention from the internal `SessionConfig`/
`ProcessConfig` Python attributes (snake_case, no dots — `thinking_token_budgets`,
`prompt_input_max_lines`), which follow ordinary Python style instead. `SESSION_KEY_MAP` and
`PROCESS_KEY_MAP` in `process_config.py` are the single source of truth translating one to
the other; there is deliberately no attempt to derive one name from the other
mechanically, since the on-disk convention (dotted, camelCase, grouped by feature) and the
Python convention (snake_case, flat) diverge by design. Adding a new config-file-exposed
setting means adding one entry to the relevant map, not renaming the underlying Python
field — **and** adding that key, at its shipped default value, to
`klorb/src/klorb/resources/default-config.json`. That file (not a bare pydantic field
default) is the canonical, always-merged source of a setting's built-in default going
forward — see "How it works" above — so a new setting with no entry there would only get its
Python field default until *some* config layer happened to set it explicitly.

Two other, differently-scoped JSON files are easy to confuse with `default-config.json`:

* `klorb/src/klorb/resources/template-config.json` — a second packaged resource, but never
  read as a config layer *itself*. It's a deliberately spartan starter file (most keys
  omitted, so it reads as "here's what's worth touching," not "here's every key") that
  `klorb init` (see [[klorb-init]]) copies to `/etc/klorb/klorb-config.json` or
  `$KLORB_CONFIG_DIR/klorb-config.json` for a user to hand-edit. Once copied, *that* on-disk
  copy is an ordinary layer 2/3 file, indistinguishable from a hand-authored one — but the
  packaged `template-config.json` resource itself never is.
* `.klorb/klorb-config.json` in *this* repo's own root — an ordinary per-project config
  layer (layer 4 above) for developing klorb itself, unrelated to either packaged resource.

## Configuration

* `klorb-config.json` — see "How it works" above for the six layers and their
  precedence, and "On-disk key naming" above for the file's shape and key style. Every entry
  in `SESSION_KEY_MAP` (`model`, `thinking.enabled`, `thinking.effort`, `tools.maxCallsPerTurn`,
  `tools.maxCallsPerSession`) can be set inside `sessionDefaults`; every entry in
  `PROCESS_KEY_MAP` (`thinking.tokenBudgets`, `terminal.input.maxLines`,
  `tools.readFile.maxLines`, `tools.editFile.driftSearchRadius`, `tools.grep.maxResults`,
  `tools.findFile.maxResults`, `providers.openrouter.baseUrl`, `shell.command`,
  `shell.timeout`, `compatibility.claudeMarkdown`) can be set at the top level.
  `thinking.tokenBudgets`, being a nested object (`{"low": ...,
  "medium": ..., "high": ...}`), is replaced wholesale by a config layer that sets it —
  there's no per-key deep merge, so a layer overriding it must repeat every `ThinkingEffort`
  key it wants to keep. `interactive` cannot be set from a config file at all: it's always
  inferred from CLI flags (`-m`/`--interactive`/`--no-interactive`), so a
  `sessionDefaults.interactive` key is dropped with a warning like any other unrecognized
  key.
* `sessionDefaults.readDirs`/`writeDirs` are handled separately from `SESSION_KEY_MAP` — they
  merge by concatenation, not the scalar replacement `_route_keys()` implements — see
  docs/specs/permissions.md. `workspace` cannot be set from a config file at all, by design, for
  the same reason `interactive` is CLI-only: unlike `interactive`, though, there's no CLI flag
  for it either — it's resolved by `klorb.workspace.TrustManager` and threaded into
  `load_process_config(workspace=...)` instead; see docs/specs/projects-and-trust.md.
* `$KLORB_ETC_CONFIG` — overrides the `/etc`-scope config file's path (see "How it works").
* `--config FILE` — layers one more `klorb-config.json`-shaped file on top of the packaged
  and fixed-path layers, below CLI flags.
* `--model MODEL` — overrides `sessionDefaults.model` for this process, on top of every
  config file layer.

## Out of scope

* Adding another process-only field requires adding it to `ProcessConfig` and adding its
  on-disk key to `PROCESS_KEY_MAP` (or `SESSION_KEY_MAP`, for a new `SessionConfig` field),
  plus a shipped-default entry in `default-config.json` — the two maps aren't inferred from
  each other by design, and neither map is inferred from `default-config.json` either (see
  "On-disk key naming" above).
* `read_file_max_lines` (`tools.readFile.maxLines`) is consumed by `ReadFileTool` via
  `context.process_config.read_file_max_lines`, where `context` is the `ToolSetupContext`
  every `Tool` (`klorb.tools.tool.Tool`) is constructed with — see [[tool-framework]].
  `DEFAULT_READ_FILE_MAX_LINES` in `process_config.py` is its sole canonical default;
  `klorb.tools.read_file` has no constant of its own — see
  [the ToolSetupContext ADR](../adrs/tool-setup-context-carries-process-and-session-config.md).
* `edit_file_drift_search_radius` (`tools.editFile.driftSearchRadius`) is consumed the same way
  by `EditFileTool` via `context.process_config.edit_file_drift_search_radius`;
  `DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS` in `process_config.py` is its sole canonical default —
  see [[tool-framework]] and
  [the drift-tolerance ADR](../adrs/edit-file-tolerates-bounded-line-drift-via-local-candidate-search.md).
* `grep_max_results` (`tools.grep.maxResults`) and `find_file_max_results`
  (`tools.findFile.maxResults`) are consumed the same way by `GrepTool`/`FindFileTool` via
  `context.process_config.grep_max_results`/`find_file_max_results`; `DEFAULT_GREP_MAX_RESULTS`/
  `DEFAULT_FIND_FILE_MAX_RESULTS` in `process_config.py` are their sole canonical defaults — see
  [[tool-framework]].
* `last-session.json` (`TODO.md`) doesn't exist yet; `_load_last_session_overrides()` is a
  placeholder that always returns no overrides.
* Provider selection isn't implemented (no command exists to change it), so there's nothing
  to dual-write for it yet; `openrouter_base_url` is process-only precisely because there's
  no per-session notion of "provider" to attach it to.
* Concurrent sessions within one process aren't implemented yet; `ProcessConfig` is
  designed for that future but only one `Session` is ever live today.
* `readDirs`/`writeDirs`, `workspace.trusted`, and everything else about how file-tool
  permissions are actually evaluated (category order, the workspace-root hard boundary, the
  `${workspace_root}/.klorb/` implicit deny, known risks) is out of scope for this spec — see
  docs/specs/permissions.md. How `workspace` itself gets resolved (registration, the interactive
  trust bootstrap) is out of scope too — see docs/specs/projects-and-trust.md.
