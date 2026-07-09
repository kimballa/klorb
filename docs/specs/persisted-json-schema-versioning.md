# Persisted JSON schema versioning

## Summary

Every klorb JSON file that's written to disk and expected to be read back later — by a
future run of the same process type, not just consumed once — carries a small envelope
identifying its file type and format version. This is framework-level: any feature that
persists state (config files today; saved session state, per the `last-session.json` item in
`TODO.md`, in the future) reads through the shared helper in `klorb.schema_envelope` rather
than parsing JSON directly, so upgrade handling only needs to be written once. See
[the schema envelope ADR](../adrs/version-persisted-json-with-a-schema-envelope.md) for why
this exists.

## How it works

* The envelope is a top-level `schema` key sitting alongside a file's actual data, in the
  same JSON object:

  ```json
  {
    "schema": {"name": "klorb-config", "version": "1.0.0"},
    "sessionDefaults": {"model": "openai/gpt-5-nano"}
  }
  ```

  `name` identifies the file type (`klorb-config`, and eventually `klorb-session`); `version`
  is a semver string for that type's format.
* `klorb.schema_envelope` (`klorb/src/klorb/schema_envelope.py`) exposes:
  * `SchemaInfo` — a pydantic `BaseModel` with `name: str` and `version: str`, used to
    validate the `schema` block.
  * `read_versioned_json(path: Path, *, expected_schema_name: str) -> dict[str, Any]` —
    reads `path`, pops its `schema` key, and returns the remaining data:
    * If `path` doesn't exist, returns `{}` (logged at debug level). This is expected, not
      an error — see [[process-and-session-config]] for why config files in particular are
      optional at every layer.
    * If the file has no `schema` block at all, its data is returned as-is (logged at debug
      level). Hand-authored files like `klorb-config.json` may omit it; this is tolerated
      rather than rejected.
    * If `schema.name` doesn't match `expected_schema_name`, the file's data is discarded
      (returns `{}`) and a warning is logged — it's probably the wrong file type in the
      wrong place.
    * If the file's contents aren't valid JSON at all, the data is likewise discarded
      (returns `{}`) and an error is logged naming the file, the parse failure, and a small
      excerpt of the lines around it (`_format_json_error_context`), rather than raising and
      taking down the caller. This matters most for `klorb-config.json`, which is hand-authored
      and layered — see [[process-and-session-config]] — so a typo in one layer must not
      prevent every other layer (and the process) from loading. Passing a `warnings: list[str]`
      collects this same message so a caller can surface it somewhere a user will actually
      see it, not just the log — `klorb.process_config.load_process_config` does this via
      `ProcessConfig.config_warnings`, which `klorb.tui.repl.ReplApp` posts to the history
      scroll at startup.
    * There is currently no version-upgrade logic: every schema in use today is `"1.0.0"`.
      When a format changes, the upgrade path belongs inside `read_versioned_json` (or a
      dedicated per-type migration step it calls out to), keyed on `schema.version`, so
      every caller benefits without change.

## Usage

* Any code that writes a persisted JSON file must include the `schema` envelope shown
  above.
* Any code that reads one back calls `read_versioned_json()` rather than `json.load()`
  directly.
* `klorb.process_config` is the first consumer — see
  [[process-and-session-config]].
* This envelope convention is orthogonal to *key naming* within a file's data. User-facing,
  hand-authored config files (`klorb-config.json`) use dot-delineated, lowerCamelCase keys
  (`"thinking.tokenBudgets"`, VSCode/Claude Code settings-file style) grouped under a nested
  `sessionDefaults` object for session-scoped settings, rather than the snake_case,
  flattened-together shape of the Python attributes they set — see
  [[process-and-session-config]]'s "On-disk key naming" section for the full convention and
  why it's kept independent of internal Python naming.

## Out of scope

* No upgrade/migration logic exists yet; there's only ever been one version of any schema.
* No write helper exists yet, since every current writer (a user hand-authoring
  `klorb-config.json`) writes its own file directly. A `last-session.json` writer, when
  built, is the natural place to add one if the envelope-construction boilerplate turns out
  to be worth sharing.
