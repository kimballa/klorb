# Pick models for klorb-owned tasks by a capability flag, not a hardcoded model name

* Date: 2026-07-12 12:30
* Question: Code that needs a model for its own purposes — not the main conversation, which
  the user picks via "Change model" — has so far just hardcoded a model name as a config
  default (`ProcessConfig.bash_risk_classifier_model` defaulted to the literal
  `"openai/gpt-5-nano"`). As klorb grows more of these internal, model-driven tasks (a bash
  risk classifier today; plausibly others later — a commit-message summarizer, a diff
  reviewer), should each one keep hardcoding its own literal model name, or is there a better
  way for klorb to pick a model for a task it owns?
* Answer: Every `klorb-model` JSON file may declare a `klorb_capabilities` object: a map of
  klorb-curated flags (usually `bool`) naming tasks that model is especially suited for — e.g.
  `{"BASH_SAFETY_EVAL": true}` on `openai/gpt-oss-safeguard-20b:nitro`, a safety-reasoning
  model built for exactly this kind of classification. `Model.klorb_capabilities() -> dict[str,
  Any]` exposes it (default `{}`); `ModelRegistry.find_by_capability(capability) -> Model |
  None` returns the first registered model (by name) that declares it truthily. A config field
  that picks a model for klorb's own use (`ProcessConfig.bash_risk_classifier_model`) now
  defaults to `None`/unset rather than a literal string; `resolve_item_risk_assessment` calls
  `find_by_capability("BASH_SAFETY_EVAL")` when it's unset, falling back to a last-resort
  literal (`DEFAULT_BASH_RISK_CLASSIFIER_MODEL`) only if no registered model declares the
  capability. An explicit user-configured value always wins over the lookup — a user pointing
  any task at any model always "just works," the flag only informs klorb's *own* default.
* Reasoning: A hardcoded model name goes stale the moment a better-suited model ships — nothing
  updates `DEFAULT_BASH_RISK_CLASSIFIER_MODEL` to point at
  `openai/gpt-oss-safeguard-20b:nitro` automatically just because it exists now, and the same
  problem would recur for every future klorb-owned task that needs "a cheap, safety-focused
  model" or "a large-context model" or any other property. A capability flag on the model's own
  data, looked up at the point of use, means adding a new candidate model (or swapping the
  built-in one for a newer release) is a one-line JSON change — no code in
  `klorb.permissions.risk_classifier` (or any future capability-driven consumer) needs to know
  which specific model currently holds the title. `klorb_capabilities` stays deliberately
  separate from `capabilities()`: the latter is what OpenRouter/the provider reports about the
  model (vision, context window, ...) — objective facts — while `klorb_capabilities` is a
  klorb-curated, subjective judgment about fitness for specific klorb-internal tasks, and
  conflating the two would make `capabilities()` no longer a faithful mirror of what the
  provider actually publishes.
