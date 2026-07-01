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
  four process-only fields today: `prompt_input_max_lines` (int, default `12` — the REPL
  prompt textarea's max grow height, applied via `input_widget.styles.max_height` in
  `ReplApp.on_mount()`), `thinking_token_budgets` (`dict[ThinkingEffort, int]`, default
  `session.THINKING_EFFORT_TOKEN_BUDGETS` — the token budgets `Session._reasoning_params()`
  looks `config.thinking_effort` up in for `"tokens"`-style models), `read_file_max_lines`
  (int, default `DEFAULT_READ_FILE_MAX_LINES`, `200` — `ReadFileTool`'s per-call page size;
  see "Out of scope" below), and `openrouter_base_url` (str, default
  `openrouter.OPENROUTER_BASE_URL` — passed to `OpenRouterApiProvider(base_url=...)`, for
  pointing at a self-hosted or corporate OpenRouter-compatible gateway).

  `session` is a *template*, not a shared instance — every `Session` created in the process
  (at startup, or via `/clear`) gets `process_config.session.model_copy()`, an independent
  copy it can mutate freely without affecting any other session or the template itself. The
  process-only fields, by contrast, are read directly wherever they're needed (e.g.
  `self._process_config.prompt_input_max_lines`) rather than copied, since they must be
  identical across every concurrently running session.
* `load_process_config(*, config_flag_path: Path | None = None, cwd: Path | None = None) ->
  ProcessConfig` assembles the config once at process startup by layering plain JSON
  objects, each read via
  [`read_versioned_json`](persisted-json-schema-versioning.md), in increasing order of
  precedence:
  1. `/etc/klorb/klorb-config.json`, or the file named by `$KLORB_ETC_CONFIG` if that env
     var is set. Resolved lazily, at call time rather than import time, specifically so a
     value set via a `.env` file (loaded by `klorb.cli.main()`'s `load_dotenv()` call before
     `load_process_config()` runs) takes effect — unlike `KLORB_CONFIG_DIR` below, which
     (per [[paths-and-logging]]) is resolved once at `klorb.paths` import time and so does
     *not* currently pick up a `.env`-supplied value.
  2. `$KLORB_CONFIG_DIR/klorb-config.json` (defaults to `~/.config/klorb`; see
     [[paths-and-logging]] for the `KLORB_CONFIG_DIR` override).
  3. `cwd/.klorb/klorb-config.json`
  4. The file named by `--config` on the command line, if given.
  5. Overrides carried over from the previous session's saved state — a placeholder for now
     (`_load_last_session_overrides()` always returns `{}`), reserved for when
     `last-session.json` (see `TODO.md`) exists.

  Each layer is one JSON object shaped like `docs/specs/persisted-json-schema-versioning.md`
  describes, plus a `sessionDefaults` key holding a nested object of session-scoped
  settings — see "On-disk key naming" below. The top-level object and the `sessionDefaults`
  object are merged independently across layers (`dict.update` on each, not a deep merge),
  so a layer can override just one session default, or just one process-only setting,
  without repeating everything else. A missing file at any layer is silently skipped —
  that's the expected common case, not an error — and logged at debug level either way, so a
  user debugging "why didn't my config apply" can see what was and wasn't found.

  After merging, `_route_keys()` looks each on-disk key up in `SESSION_KEY_MAP` (for
  `sessionDefaults` keys) or `PROCESS_KEY_MAP` (for top-level keys) — both in
  `process_config.py` — to find which internal attribute it sets; a key with no entry in the
  relevant map is dropped with a warning logged (almost certainly a typo, not an
  intentionally-unrecognized setting).
* CLI flags (other than `--config` itself, which selects a layer above) are applied by
  `klorb.cli.main()` directly on top of the loaded `ProcessConfig`, since only `cli.py` knows
  the argparse `Namespace` shape (see `CLAUDE.md`'s CLI/library firewall rule). Today that's
  `--model`, whose argparse default is `None` (not `openai/gpt-4o-mini`) specifically so
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
    "model": "openai/gpt-4o-mini",
    "thinking.enabled": true,
    "thinking.effort": "high"
  },

  "thinking.tokenBudgets": {"low": 4096, "medium": 16384, "high": 32768},
  "terminal.input.maxLines": 12,
  "tools.readFile.maxLines": 200,
  "providers.openrouter.baseUrl": "https://openrouter.ai/api/v1"
}
```

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
field. A reference file with every recognized key at its current default lives at
`etc/klorb-config.json` in the repo root.

## Configuration

* `klorb-config.json` — see "How it works" above for the five file locations and their
  precedence, and "On-disk key naming" above for the file's shape and key style. Every entry
  in `SESSION_KEY_MAP` (`model`, `thinking.enabled`, `thinking.effort`) can be set inside
  `sessionDefaults`; every entry in `PROCESS_KEY_MAP` (`thinking.tokenBudgets`,
  `terminal.input.maxLines`, `tools.readFile.maxLines`, `providers.openrouter.baseUrl`) can
  be set at the top level. `thinking.tokenBudgets`, being a nested object (`{"low": ...,
  "medium": ..., "high": ...}`), is replaced wholesale by a config layer that sets it —
  there's no per-key deep merge, so a layer overriding it must repeat every `ThinkingEffort`
  key it wants to keep. `interactive` cannot be set from a config file at all: it's always
  inferred from CLI flags (`-m`/`--interactive`/`--no-interactive`), so a
  `sessionDefaults.interactive` key is dropped with a warning like any other unrecognized
  key.
* `$KLORB_ETC_CONFIG` — overrides the `/etc`-scope config file's path (see "How it works").
* `--config FILE` — layers one more `klorb-config.json`-shaped file on top of the three
  fixed locations, below CLI flags.
* `--model MODEL` — overrides `sessionDefaults.model` for this process, on top of every
  config file layer.

## Out of scope

* Adding another process-only field requires adding it to `ProcessConfig` and adding its
  on-disk key to `PROCESS_KEY_MAP` (or `SESSION_KEY_MAP`, for a new `SessionConfig` field) —
  the two aren't inferred from each other by design (see "On-disk key naming" above).
* `read_file_max_lines` (`tools.readFile.maxLines`) is consumed by `ReadFileTool` via
  `context.process_config.read_file_max_lines`, where `context` is the `ToolSetupContext`
  every `Tool` (`klorb.tools.tool.Tool`) is constructed with — see [[tool-framework]]. Its
  default (`DEFAULT_READ_FILE_MAX_LINES` in `process_config.py`) is a literal duplicating
  `klorb.tools.read_file.MAX_LINES`, not imported from it, to avoid a circular import between
  `klorb.process_config` and `klorb.tools` — see
  [the ToolSetupContext ADR](../adrs/tool-setup-context-carries-process-and-session-config.md).
* `last-session.json` (`TODO.md`) doesn't exist yet; `_load_last_session_overrides()` is a
  placeholder that always returns no overrides.
* Provider selection isn't implemented (no command exists to change it), so there's nothing
  to dual-write for it yet; `openrouter_base_url` is process-only precisely because there's
  no per-session notion of "provider" to attach it to.
* Concurrent sessions within one process aren't implemented yet; `ProcessConfig` is
  designed for that future but only one `Session` is ever live today.
