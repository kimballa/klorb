# © Copyright 2026 Aaron Kimball
"""Shared helpers for the schema-versioning envelope used by every klorb JSON file that gets
persisted to disk and reloaded later (config files, saved session state, etc.).

Every such file is one JSON object with a top-level `schema` key alongside its data:

```json
{
  "schema": {"name": "klorb-config", "version": "1.0.0"},
  "model": "openai/gpt-5-nano"
}
```

`name` identifies the file type and `version` its format, so a future klorb version can
detect an old file and upgrade it instead of failing to parse it. See
docs/specs/persisted-json-schema-versioning.md for the full convention.
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

SCHEMA_KEY = "schema"


class SchemaInfo(BaseModel):
    """Identifies a persisted JSON file's type (`name`) and format (`version`)."""

    name: str
    version: str


def parse_versioned_json(text: str, *, expected_schema_name: str, source: str) -> dict[str, Any]:
    """Parse already-read `text` as a schema-enveloped JSON document (see module docstring),
    validating and stripping its `schema` block exactly like `read_versioned_json` does for an
    on-disk file — this is what that function delegates to once it has the file's contents in
    hand, and it's also what a caller reading a *packaged* resource via `importlib.resources`
    (which yields text, not a `Path`) should use directly, e.g.
    `klorb.process_config`'s built-in-defaults layer. `source` identifies `text`'s origin
    (a `Path`, or a resource name) for the log messages below only.

    Text that isn't valid JSON at all (a hand-edited config file with a typo, a torn write from
    a crashed process, etc.) is treated the same as a schema-name mismatch: an error is logged
    naming `source` and the parse exception, and `{}` is returned rather than letting
    `json.JSONDecodeError` propagate — one malformed layer must not take down the whole process,
    since every caller of this helper merges several independently-sourced layers (see
    `klorb.process_config.load_process_config`) where the rest are still worth loading.
    """
    try:
        contents: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.error("%s is not valid JSON (%s); ignoring its contents.", source, exc)
        return {}

    schema_block = contents.pop(SCHEMA_KEY, None)
    if schema_block is None:
        logger.debug("%s has no schema block; treating all keys as data.", source)
        return contents

    schema_info = SchemaInfo.model_validate(schema_block)
    if schema_info.name != expected_schema_name:
        logger.warning(
            "%s declares schema name %r, expected %r; ignoring its contents.",
            source, schema_info.name, expected_schema_name)
        return {}

    logger.debug("Loaded %s (schema %s v%s).", source, schema_info.name, schema_info.version)
    return contents


def read_versioned_json(path: Path, *, expected_schema_name: str) -> dict[str, Any]:
    """Read a schema-enveloped JSON file's data, or `{}` if `path` doesn't exist.

    The `schema` key, if present, is validated against `expected_schema_name` and then
    stripped; a name mismatch logs a warning and discards the rest of the file's contents,
    since it's probably the wrong file type. A file with no `schema` block at all is still
    accepted, with its keys returned as-is, since files like `klorb-config.json` are
    hand-authored and may omit it; this is logged at debug level rather than treated as an
    error. A file that isn't valid JSON at all logs an error and is likewise discarded (`{}`)
    rather than raising — see `parse_versioned_json`, which this delegates to.
    """
    if not path.is_file():
        logger.debug("No file at %s; skipping.", path)
        return {}

    return parse_versioned_json(
        path.read_text(encoding="utf-8"), expected_schema_name=expected_schema_name, source=str(path))


def write_versioned_json(
    path: Path, data: dict[str, Any], *, schema_name: str, schema_version: str,
) -> None:
    """Write `data` to `path` as a schema-enveloped JSON file (see module docstring),
    creating `path`'s parent directory if it doesn't exist yet.

    `data` must not itself contain a `SCHEMA_KEY` ("schema") key — that would silently collide
    with, and be shadowed by, the envelope's own `schema` block; raises `ValueError` if it does,
    rather than silently discarding the caller's key.

    Writes atomically: the full contents are written to a temporary file in `path`'s own parent
    directory (so the final `os.replace()` is same-filesystem and atomic), then renamed onto
    `path`. This matters because `path` may be read again moments later by another tool call in
    the same turn — a process interrupted mid-write must never leave a torn, unparseable config
    file behind.
    """
    if SCHEMA_KEY in data:
        raise ValueError(f"data already contains a {SCHEMA_KEY!r} key; refusing to overwrite it")

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {SCHEMA_KEY: {"name": schema_name, "version": schema_version}, **data}

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            json.dump(payload, tmp_file, indent=2)
            tmp_file.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise
    logger.debug("Wrote %s (schema %s v%s).", path, schema_name, schema_version)
