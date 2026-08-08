# Ship system prompts as package data, with user overrides in `$KLORB_CONFIG_DIR`

* Date: 2026-07-03 19:56
* Question: System prompts are moving out of Python string literals into editable markdown
  files. Where do the built-in defaults live — an uninstalled `etc/`-style reference tree in
  the repo (like `etc/klorb-config.json`), files copied into the user's homedir at install
  time, or data files inside the installed package? And does the lookup hierarchy need a
  per-provider level (e.g. an `openrouter/` directory), given prompts may be tuned
  per-model?
* Answer: The built-in defaults are real package data: they live at
  `klorb/src/klorb/resources/system_prompts.d/` in the source tree, are declared under
  `[tool.setuptools.package-data]` in `klorb/pyproject.toml`, and are read at runtime via
  `importlib.resources.files("klorb.resources")`. User-editable overrides mirror the same
  tree shape under `$KLORB_CONFIG_DIR/system_prompts.d/` (default `~/.config/klorb`), and
  the user tier always beats the packaged tier for the same relative path
  (`klorb.system_prompts.resolve_prompt_file()`). There is no `/etc`-level tier and no
  install-time copying. There is no provider level in the tree: model-tuned prompts are
  keyed by the model's identifier alone, mangled into a filename stem by replacing `/` and
  `:` with `__` (`poolside/laguna-m.1:free` → `poolside__laguna-m.1__free.md`).
* Reasoning: The `etc/klorb-config.json`-stays-uninstalled precedent (see `TODO.md`'s
  `klorb init` item) exists because wheels can't reliably install files to *absolute host
  paths* like `/etc/klorb` or `~/.config/klorb` — but prompt defaults don't need a host
  path, only to exist *inside the package*, which `package-data` + `importlib.resources`
  handles reliably. Shipping them in the wheel means a pip-installed klorb always has its
  defaults, with no `klorb init` step as a prerequisite for basic operation. Overrides go
  under `$KLORB_CONFIG_DIR` (config) rather than `$KLORB_DATA_DIR` (`~/.local/share`,
  data): hand-edited prompt text is configuration, the same category as
  `klorb-config.json` itself, whereas the data dir is reserved for cached/generated
  artifacts ([[paths-and-logging]]). A homedir-share install-time copy was rejected for the
  same reason the `/etc` config copy was (no reliable packaging mechanism), and an `/etc`
  prompt tier was skipped as a third tree to manage with no current need — the layered
  process-config machinery can gain one later if system-wide prompt policy ever matters.
  The provider level was dropped because a model is the same model regardless of which API
  gateway serves it — Sonnet via OpenRouter is Sonnet via Anthropic — so a prompt tuned to
  a model's behavior applies through any provider; model identifiers are already
  vendor-qualified strings, so the mangled stem stays collision-free without a provider
  directory namespacing it. A gateway quirk severe enough to need different prompt text
  would just be a model-specific override like any other.
