# Back models with JSON resource files, not one Python class per model

* Date: 2026-07-12 10:00
* Question: `klorb.models` originally defined one hand-written `Model` subclass per model
  (`Gpt5NanoModel`, `Glm52Model`, `ClaudeSonnet5Model`), each a small, near-identical file of
  `name()`/`settings()`/`capabilities()` literals. Adding new models (a coding-specific Qwen
  release, a coding-specific Kimi release) and new per-model metadata (family, version,
  pricing) would mean writing yet more of these boilerplate classes. Should new models keep
  arriving as Python classes, or should model data move to a declarative format?
* Answer: Model data lives in schema-enveloped JSON files (schema name `klorb-model`), one
  file per model, discovered from two directories: the packaged `klorb.resources.models`
  tree (built-in models) and `$KLORB_DATA_DIR/models` (user-supplied models). A single
  concrete `klorb.models.configured_model.ConfiguredModel` class implements the `Model`
  interface by reading its fields straight out of a parsed JSON dict. `ModelRegistry` scans
  both directories and builds one `ConfiguredModel` per file instead of importing Python
  modules and instantiating discovered `Model` subclasses.
* Reasoning: A model's data — its OpenRouter identifier, family/version, capability flags,
  default settings, and pricing — has no behavior of its own; every existing per-model class
  was pure data with no logic beyond returning a literal. Writing a new Python module (plus
  its own test file asserting the same literals round-trip) for each new model is pure
  ceremony that JSON authoring avoids entirely. A JSON file is also what a user can drop into
  `$KLORB_DATA_DIR/models` to register a custom or locally-hosted model without touching
  klorb's source at all — a Python-class registry has no equivalent extension point short of
  a plugin-loading mechanism. The `klorb-model` schema envelope follows the same
  versioning convention as every other persisted klorb JSON file (see
  [[persisted-json-schema-versioning]]), so a future format change can be detected and
  migrated rather than silently misread. `Model` stays an abstract base class (rather than
  being deleted in favor of a bare dict) so a future model type that genuinely needs custom
  behavior — a locally-run model with its own request-shaping logic, say — still has an
  interface to implement; `ConfiguredModel` is simply the one concrete implementation in use
  today.
