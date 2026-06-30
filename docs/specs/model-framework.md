# Model framework

## Summary

A `Model` describes a model klorb can send prompts to: its identifier, its system prompt,
its provider-specific settings/flags, and a dict of its capabilities (vision, thinking, max
context window, etc.). `ModelRegistry` discovers `Model` implementations the same way
[[tool-framework]]'s `ToolRegistry` discovers tools. The REPL exposes the discovered models
through Textual's command palette so the user can switch models interactively. See
[[use-textuals-command-palette-for-model-selection]] for why the command palette was used
instead of a bespoke picker widget.

## How it works

* `klorb.models.model.Model` (`klorb/src/klorb/models/model.py`) is an abstract base class.
  Concrete models implement:
  * `name() -> str` — the model's identifier, as used by the API provider (e.g. an
    OpenRouter model string) and as the value shown/selected in the command palette.
  * `system_prompt() -> str` — the system prompt to send to this model.
  * `settings() -> dict[str, Any]` — provider-specific settings/flags to send alongside
    requests to this model (e.g. `temperature`).
  * `capabilities() -> dict[str, Any]` — a dict describing the model's capabilities.
    Standard keys include `vision` (bool), `thinking` (bool), and `max_context_window`
    (int, in tokens); implementations may add further provider-specific keys.
* `klorb.models.registry.ModelRegistry` (`klorb/src/klorb/models/registry.py`) discovers
  `Model` subclasses by walking a package's modules with `pkgutil.iter_modules`, importing
  each, and collecting concrete (non-abstract) `Model` subclasses defined directly in that
  module. By default it scans the `klorb.models` package itself, so dropping a new module
  containing a `Model` subclass into `klorb/src/klorb/models/` is enough to register it — no
  manual registration step is required. A different package can be passed to the
  constructor (used by tests to scan a fixture package instead).
  * `models() -> list[Model]` — all discovered models.
  * `get(name: str) -> Model` — look up a discovered model by name.
* `klorb.models.gpt_4o_mini.Gpt4oMiniModel` (`klorb/src/klorb/models/gpt_4o_mini.py`) is the
  built-in `Model` for `openai/gpt-4o-mini`, klorb's current default model
  (`klorb.openrouter.DEFAULT_MODEL`).
* `klorb.tui.model_commands.ModelCommandProvider`
  (`klorb/src/klorb/tui/model_commands.py`) is a Textual `command.Provider`. It builds a
  fresh `ModelRegistry()` per search, lists a `"Select model: <name>"` command per
  discovered model, and on selection calls `select_model(name)` on the active app (matched
  structurally via a `SupportsModelSelection` protocol, so this module doesn't need to
  import `klorb.tui.repl` and risk a circular import).
  `ReplApp.COMMANDS` (`klorb/src/klorb/tui/repl.py`) includes `ModelCommandProvider`
  alongside Textual's default system commands, so pressing `Ctrl+P` and typing a model name
  (or browsing the default list) switches models.
  `ReplApp.select_model(name)` updates the model used for subsequent
  `ApiProvider.send_prompt()` calls, updates the window's `sub_title` to the new model name
  (shown in the `Header` widget), and shows a toast notification confirming the switch.
* [[session-and-turns]]'s `Session.active_model_name()` looks up `SessionConfig.model` in a
  `ModelRegistry` and, when it's registered, calls the resulting `Model.name()` to get the
  identifier passed to `ApiProvider.send_prompt()`; an unregistered model string is passed
  through unchanged, so `--model` can still target any OpenRouter model identifier without
  a `Model` implementation.

## Out of scope

* Wiring a `Model`'s `system_prompt()` and `settings()` into the actual
  `ApiProvider.send_prompt()` call is future work — today only `name()` is used (as the
  `model` argument), mirroring [[openrouter-prompt-client]]'s single-shot, no-system-prompt
  request shape.
* Recursive discovery into subpackages of `klorb.models`, and additional built-in `Model`
  implementations beyond `openai/gpt-4o-mini`, are not implemented yet.
