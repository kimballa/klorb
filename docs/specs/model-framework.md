# Model framework

## Summary

A `Model` describes a model klorb can send prompts to: its identifier, family/version,
provider-specific settings/flags, a dict of its capabilities (vision, thinking, max context
window, etc.), and its published per-token pricing when known. Every model klorb ships with,
plus any a user adds, is described declaratively by a `klorb-model` JSON file rather than a
hand-written Python class — see
[[back-models-with-json-resource-files-not-python-classes]]. `ModelRegistry` discovers these
JSON files the same way [[tool-framework]]'s `ToolRegistry` discovers tools, by scanning
directories rather than importing hand-registered modules. The REPL exposes the discovered
models through a single "Change model" command that opens a filterable modal, plus a "Show
model info" command — see [[single-change-model-command-with-typeahead-modal]] for why model
selection isn't just another set of entries in the default command palette listing.

## How it works

* `klorb.models.model.Model` (`klorb/src/klorb/models/model.py`) is an abstract base class.
  Concrete models implement:
  * `name() -> str` — the model's identifier, as used by the API provider (e.g. an
    OpenRouter model string) and as the value shown/selected in the "Change model" modal.
  * `settings() -> dict[str, Any]` — provider-specific settings/flags to send alongside
    requests to this model (e.g. `temperature`).
  * `capabilities() -> dict[str, Any]` — a dict describing the model's capabilities.
    Standard keys include `vision` (bool), `thinking` (bool), `thinking_budget_style`
    (a `klorb.models.model.ThinkingBudgetStyle`: `"effort"` or `"tokens"`, meaningful only
    when `thinking` is `True` — whether the model's reasoning depth is tuned via a
    low/medium/high effort keyword or a numeric reasoning token budget; defaults to
    `"effort"` if omitted, see [[session-and-turns]]), `max_context_window` (int, in
    tokens), `max_output_tokens` (int), `function_calling` (bool), and `streaming` (bool);
    implementations may add further provider-specific keys.

  The base class additionally provides:
  * `mangled_name() -> str` and `system_prompt() -> str | None`, used by system-prompt
    resolution (see [[roles-and-system-prompts]]): the identifier made filesystem-safe
    (`/` and `:` become `__`), and the model's role-agnostic `<mangled_name>.md` prompt file
    from a `system_prompts.d/` tree (or `None` if no such file exists; subclasses — e.g.
    test fixtures — may override `system_prompt()` to return a literal string without
    filesystem access).
  * `family() -> str | None` and `model_version() -> str | None`, concrete methods
    returning `None` by default. An OpenRouter model identifier often conflates a family
    name with its version number — `"anthropic/claude-sonnet-5"` mixes the `"sonnet"` tier
    with version `"5"` — so these tease the two apart, letting a caller detect that e.g.
    `"anthropic/claude-sonnet-5"` and a future `"anthropic/claude-sonnet-5.1"` share a
    family even though their full names differ.
  * `pricing() -> ModelPricing | None`, a concrete method returning `None` by default.
    `klorb.models.model.ModelPricing` is a pydantic model with `input_cost_per_mtok`,
    `output_cost_per_mtok` (both `float`, cost per million tokens), and `currency`
    (`str`, defaults to `"USD"`).
* `klorb.models.configured_model.ConfiguredModel`
  (`klorb/src/klorb/models/configured_model.py`) is the one concrete `Model` implementation
  in use: it wraps a parsed `klorb-model` JSON document (schema name `klorb-model`, see
  [[persisted-json-schema-versioning]]) and returns each field straight from it. Its
  constructor takes the already schema-envelope-stripped data dict plus a `source` string
  (the file path or packaged resource name the data came from, for error messages/debugging,
  exposed via `source()`). A `klorb-model` document has this shape:

  ```json
  {
    "schema": {"name": "klorb-model", "version": "1.0.0"},
    "name": "anthropic/claude-sonnet-5",
    "family": "claude-sonnet",
    "model_version": "5.0",
    "settings": {"temperature": 0.2},
    "capabilities": {
      "vision": true,
      "thinking": true,
      "thinking_budget_style": "effort",
      "max_context_window": 1000000,
      "max_output_tokens": 128000,
      "function_calling": true,
      "streaming": true
    },
    "pricing": {"input_cost_per_mtok": 2.0, "output_cost_per_mtok": 10.0, "currency": "USD"}
  }
  ```

  `pricing` is optional and omitted entirely when a model's cost isn't known/published.
  Every JSON key besides `schema` uses the same snake_case as the `Model.capabilities()`
  dict it's read into (rather than the dot-delineated lowerCamelCase
  [[process-and-session-config]] uses for the hand-authored `klorb-config.json`), so no key
  translation happens between the file and the dict callers read.
* `klorb.models.registry.ModelRegistry` (`klorb/src/klorb/models/registry.py`) discovers
  `klorb-model` JSON files by scanning two directories in order and building one
  `ConfiguredModel` per file, keyed by its `name` field:
  1. The packaged built-in models tree, `klorb.resources/models/*.json` (read via
     `importlib.resources`, shipped in the wheel per `pyproject.toml`'s
     `[tool.setuptools.package-data]`).
  2. The user-writable override tree, `$KLORB_DATA_DIR/models/*.json` (see
     `klorb.paths.KLORB_DATA_DIR`, default `~/.local/share/klorb/models`). A user model file
     whose `name` matches a packaged one replaces it; any other file adds a new model. This
     is the same packaged-tier-then-user-override-tier convention
     `klorb.system_prompt.resolve_prompt_file` uses, except both tiers are scanned wholesale
     here rather than one relative path being looked up in each.

  A file that fails schema validation is skipped (logged by
  `klorb.schema_envelope.parse_versioned_json`) rather than raising, so one malformed model
  file doesn't prevent every other model from loading. A different pair of directories can
  be passed to the constructor (used by tests to scan fixture directories instead).
  * `models() -> list[Model]` — all discovered models.
  * `get(name: str) -> Model` — look up a discovered model by name.
* klorb ships five built-in models as `klorb.resources/models/*.json`:
  `openai/gpt-5-nano` (klorb's default model, `klorb.openrouter.DEFAULT_MODEL`),
  `z-ai/glm-5.2`, `anthropic/claude-sonnet-5`, `qwen/qwen3-coder-next` (Qwen's
  coding-agent-focused model), and `moonshotai/kimi-k2.7-code` (Moonshot AI's
  coding-focused Kimi K2 release).
* `klorb.tui.model_commands.ModelCommandProvider` (`klorb/src/klorb/tui/model_commands.py`)
  is a Textual `command.Provider` offering a single `"Change model (<current>)"` command.
  Selecting it pushes `ModelSelectionScreen`, a modal with a text `Input` filter box above an
  `OptionList`: typing narrows the list via `textual.fuzzy.Matcher` (the same matcher the
  palette itself uses for ranking/highlighting), up/down arrow keys move the highlight, and
  Enter confirms — mirroring `klorb.tui.palette.PromptPalette`'s split-focus shape, where the
  `Input` keeps keyboard focus throughout and drives the `OptionList` programmatically. The
  active model is marked with a trailing `(*)`, the same convention
  `klorb.tui.theme_commands.ThemeSelectionScreen` uses for the active theme. On selection,
  `ReplApp.select_model(name)` updates the model used for subsequent
  `ApiProvider.send_prompt()` calls, updates the window's `sub_title` (shown in the `Header`
  widget), persists the choice to the per-user config file, and appends a `.notice` item to
  the history scroll confirming the switch (see [[avoid-toasts-prefer-history-notices]]).
* `klorb.tui.model_info_commands.ModelInfoCommandProvider`
  (`klorb/src/klorb/tui/model_info_commands.py`) offers a `"Show model info"` command.
  Selecting it pushes `ModelInfoScreen`, a modal rendering every field klorb tracks for the
  currently active model — name, family, version, each capability, and pricing (or
  `"(not available)"` for any field klorb doesn't know) — via
  `format_model_info(model) -> str`, which is independently testable without constructing a
  Textual app. If `config.model` isn't a registered model, the command reports that via
  `ReplApp.show_notice()` instead of opening an empty modal.
* [[session-and-turns]]'s `Session.active_model_name()` looks up `SessionConfig.model` in a
  `ModelRegistry` and, when it's registered, calls the resulting `Model.name()` to get the
  identifier passed to `ApiProvider.send_prompt()`; an unregistered model string is passed
  through unchanged, so `--model` can still target any OpenRouter model identifier without
  a `klorb-model` JSON file for it. `Session.active_model() -> Model | None` exposes the
  registered `Model` object itself (or `None` if unregistered) for callers — like
  `ModelInfoCommandProvider` — that need more than just the name.

## Out of scope

* Wiring a `Model`'s `settings()` into the actual `ApiProvider.send_prompt()` call is
  future work — `name()` is used (as the `model` argument), and `system_prompt()` is
  consulted as one tier of [[roles-and-system-prompts]]'s resolution, but `settings()` is
  not sent with requests yet.
* Recursive discovery into subdirectories of `klorb.resources/models/` or
  `$KLORB_DATA_DIR/models/` is not implemented yet; both are scanned one level deep.
* Pricing is static data captured in each `klorb-model` JSON file at authoring time, not
  fetched live from an API on every use. Refreshing it (e.g. against OpenRouter's models
  listing) is a manual, occasional maintenance task today rather than an automated one.
