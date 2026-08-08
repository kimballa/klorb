# Version every persisted JSON file with a top-level schema envelope

* Date: 2026-07-01 09:00
* Question: klorb is starting to read and write its own JSON files that outlive a single
  process run — `klorb-config.json` today, and a planned `last-session.json` (see
  `TODO.md`). If the shape of one of these files changes in a later klorb version, how
  should an old file on disk be told apart from a new one, so klorb can upgrade it instead
  of failing to parse it or silently misreading a renamed/repurposed key?
* Answer: Every such file is one JSON object with a top-level `schema` key alongside its
  data: `{"schema": {"name": "<file-type>", "version": "<semver>"}, ...data...}`. `name`
  identifies which file type it is (e.g. `klorb-config`, `klorb-session`) and `version` its
  format. `klorb.schema_envelope.read_versioned_json()` is the one shared helper that reads
  such a file, validates `schema.name` against what the caller expects, and returns the
  remaining data with the `schema` key stripped. Every future persistence feature
  (`last-session.json` included) should read through this helper rather than calling
  `json.load()` directly.
* Reasoning: A flat, unversioned JSON blob gives a future klorb version no way to detect
  that a file predates a breaking format change — it either crashes on an unexpected shape
  or, worse, silently misinterprets a key that was repurposed. A name+version envelope is
  the smallest structure that fixes this: `name` catches the case where the wrong file
  ended up in the wrong place (e.g. a `klorb-session` file misnamed into a `klorb-config.json`
  path), and `version` gives a future reader something to branch on for an upgrade path,
  without requiring one to exist yet (there's only ever been `1.0.0` so far). Centralizing
  the read in one helper means that upgrade logic, when it's eventually needed, is written
  once rather than re-derived per file type.
