# Model framework

## Summary

A `Model` describes a model klorb can send prompts to: its identifier, family/version,
provider-specific settings/flags, a dict of its capabilities (vision, thinking, max context
window, etc.), and a dict of klorb-curated capability flags (`klorb_capabilities`) naming
tasks klorb itself considers this model especially suited for. Every model klorb ships with,
plus any a user adds, is described declaratively by a `klorb-model` JSON file rather than a
hand-written Python class — see
[[back-models-with-json-resource-files-not-python-classes]]. `ModelRegistry` discovers these
JSON files the same way [[tool-framework]]'s `ToolRegistry` discovers tools, by scanning
directories rather than importing hand-registered modules. The REPL exposes the discovered
models through a single "Change model" command that opens a filterable modal, plus a "Show
model info" command — see [[single-change-model-command-with-typeahead-modal]] for why model
selection isn't just another set of entries in the default command palette listing. Per-token
pricing is deliberately *not* part of a model's stored data — see
[[fetch-model-pricing-live-not-from-json]] — and is instead fetched live, on demand, from
OpenRouter's public models listing.

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
  * `klorb_capabilities() -> dict[str, Any]`, a concrete method returning `{}` by default.
    Distinct from `capabilities()`'s raw provider-reported facts, these are klorb-curated
    flags (e.g. `{"BASH_SAFETY_EVAL": True}`) naming tasks a model is especially suited for,
    so code that picks a model programmatically can select one by capability
    (`ModelRegistry.find_by_capability`, see below) instead of a hardcoded model name. A
    user configuring any model for any task by hand always works regardless of what it
    declares here — these flags inform only klorb's own defaults, never a validation gate on
    a user's explicit choice.
  * `drop_reasoning() -> bool`, a concrete method returning `False` by default. `True` means
    this model's prior turns' thinking/reasoning content (`Message.reasoning_details`
    included) is stripped from the outgoing request rather than resent as-is —
    [[session-and-turns]]'s `Session._drop_reasoning()` reads this to decide what
    `ApiProvider.send_prompt(drop_reasoning=...)` gets for the active turn. See
    [[preserve-reasoning-across-turns-by-default]].
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
    "klorb_capabilities": {},
    "drop_reasoning": false
  }
  ```

  `drop_reasoning` is optional, defaulting to `false` when omitted — every packaged model
  today omits it. There is no `pricing` field — see
  [[fetch-model-pricing-live-not-from-json]]. Every JSON
  key besides `schema` uses the same snake_case as the `Model.capabilities()`/
  `klorb_capabilities()` dicts it's read into (rather than the dot-delineated lowerCamelCase
  [[process-and-session-config]] uses for the hand-authored `klorb-config.json`), so no key
  translation happens between the file and the dicts callers read.
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
  * `get(name: str) -> Model` — look up a discovered model by name, raising `KeyError` if
    absent.
  * `register(model: Model) -> None` — register an already-constructed `Model` directly,
    keyed by its `name()`, without discovering it from a JSON file. Used by tests to register
    `Model` test doubles without needing real `klorb-model` JSON fixture files on disk.
  * `find_by_capability(capability: str) -> Model | None` — the first registered model (by
    name, for a deterministic pick) whose `klorb_capabilities()` reports `capability`
    truthily, or `None` if none does. See `klorb.permissions.risk_classifier`'s use below.
* klorb ships six built-in models as `klorb.resources/models/*.json`:
  `openai/gpt-5-nano` (klorb's default model, `klorb.openrouter.DEFAULT_MODEL`),
  `z-ai/glm-5.2`, `anthropic/claude-sonnet-5`, `qwen/qwen3-coder-next` (Qwen's
  coding-agent-focused model), `moonshotai/kimi-k2.7-code` (Moonshot AI's coding-focused
  Kimi K2 release), and `openai/gpt-oss-120b:nitro` (OpenAI's open-weight reasoning
  model, the only built-in model declaring `klorb_capabilities.BASH_SAFETY_EVAL`).
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
  Selecting it fetches the active model's current pricing (`asyncio.to_thread`, so the
  blocking network call doesn't stall the Textual event loop — see below) and appends a
  `.notice` item to the history scroll (`ReplApp.show_notice()`, see
  [[avoid-toasts-prefer-history-notices]]) rendering every field klorb tracks for the
  currently active model — name, family, version, each capability, `klorb_capabilities`, and
  pricing (or `"(not available)"` for any field klorb doesn't know) — via
  `format_model_info(model, pricing) -> str`, which is independently testable without
  constructing a Textual app or a network connection. If `config.model` isn't a registered
  model, the command reports that via `ReplApp.show_notice()` too, as an error notice.
* `klorb.models.openrouter_pricing.fetch_openrouter_pricing(model_name) -> ModelPricing |
  None` (`klorb/src/klorb/models/openrouter_pricing.py`) is the sole source of pricing data:
  a live `GET /models` request against OpenRouter's public listing (no API key required),
  converting its dollars-per-token figures into `ModelPricing`'s dollars-per-million-tokens.
  Returns `None` — never raises — on any failure (network error, timeout, malformed
  response, model not listed). See [[fetch-model-pricing-live-not-from-json]] for why this
  is fetched live rather than stored in a model's JSON file.
* [[session-and-turns]]'s `Session.active_model_name()` looks up `SessionConfig.model` in a
  `ModelRegistry` and, when it's registered, calls the resulting `Model.name()` to get the
  identifier passed to `ApiProvider.send_prompt()`; an unregistered model string is passed
  through unchanged, so `--model` can still target any OpenRouter model identifier without
  a `klorb-model` JSON file for it. `Session.active_model() -> Model | None` exposes the
  registered `Model` object itself (or `None` if unregistered) for callers — like
  `ModelInfoCommandProvider` — that need more than just the name.
* `klorb.permissions.risk_classifier.resolve_item_risk_assessment` picks the bash-risk
  classifier's model via `ModelRegistry.find_by_capability("BASH_SAFETY_EVAL")` whenever
  `ProcessConfig.bash_risk_classifier_model` is unset (the default), falling back to
  `klorb.process_config.DEFAULT_BASH_RISK_CLASSIFIER_MODEL` if no registered model declares
  that capability. An explicit `tools.bash.riskClassifier.model` config value always wins
  over this lookup — klorb only picks for itself when nobody's told it what to use.

## Out of scope

* Wiring a `Model`'s `settings()` into the actual `ApiProvider.send_prompt()` call is
  future work — `name()` is used (as the `model` argument), and `system_prompt()` is
  consulted as one tier of [[roles-and-system-prompts]]'s resolution, but `settings()` is
  not sent with requests yet.
* Recursive discovery into subdirectories of `klorb.resources/models/` or
  `$KLORB_DATA_DIR/models/` is not implemented yet; both are scanned one level deep.
* `find_by_capability` returns the first alphabetically-named match rather than, say, the
  cheapest or fastest; with today's single built-in `"BASH_SAFETY_EVAL"`-declaring model this
  never matters in practice, but a future capability with several qualifying models would
  need a real tie-breaking rule.
