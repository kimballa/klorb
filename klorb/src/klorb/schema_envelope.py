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
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

SCHEMA_KEY = "schema"


class SchemaInfo(BaseModel):
    """Identifies a persisted JSON file's type (`name`) and format (`version`)."""

    name: str
    version: str


def read_versioned_json(path: Path, *, expected_schema_name: str) -> dict[str, Any]:
    """Read a schema-enveloped JSON file's data, or `{}` if `path` doesn't exist.

    The `schema` key, if present, is validated against `expected_schema_name` and then
    stripped; a name mismatch logs a warning and discards the rest of the file's contents,
    since it's probably the wrong file type. A file with no `schema` block at all is still
    accepted, with its keys returned as-is, since files like `klorb-config.json` are
    hand-authored and may omit it; this is logged at debug level rather than treated as an
    error.
    """
    if not path.is_file():
        logger.debug("No file at %s; skipping.", path)
        return {}

    with path.open("r", encoding="utf-8") as config_file:
        contents: dict[str, Any] = json.load(config_file)

    schema_block = contents.pop(SCHEMA_KEY, None)
    if schema_block is None:
        logger.debug("%s has no schema block; treating all keys as data.", path)
        return contents

    schema_info = SchemaInfo.model_validate(schema_block)
    if schema_info.name != expected_schema_name:
        logger.warning(
            "%s declares schema name %r, expected %r; ignoring its contents.",
            path, schema_info.name, expected_schema_name)
        return {}

    logger.debug("Loaded %s (schema %s v%s).", path, schema_info.name, schema_info.version)
    return contents
