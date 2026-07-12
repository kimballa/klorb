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
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

SCHEMA_KEY = "schema"

COMPACT_LIST_KEYS = ("allow", "ask", "deny")
"""Object keys whose list value gets one-element-per-line formatting where each element is
itself emitted on a single line, rather than the whole element tree being exploded across many
indented lines (see `_ConfigJSONEncoder`). These are the permission-rule lists —
`commandRules.allow`, `readDirs.deny`, etc. — whose elements (an argv token pattern like
`["python", "-m", "pytest", "**"]`, or a directory path string) read far better kept together on
one line than with every token on its own. See docs/specs/process-and-session-config.md's
"On-disk key naming" section."""


class _OneLine:
    """Marks a value that `_ConfigJSONEncoder` must serialize compactly on a single line even
    though the surrounding document is pretty-printed. Wrapping (rather than pre-serializing to a
    string) lets the normal encoder machinery run for the enclosing list — one wrapped element per
    indented line — while the element itself stays collapsed."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


def _wrap_compact_list_elements(node: Any) -> Any:
    """Return a copy of `node` in which every list found under a `COMPACT_LIST_KEYS` key has each
    of its elements wrapped in `_OneLine`, so `_ConfigJSONEncoder` emits that element on a single
    line. Recurses through the rest of the structure so a permission-rule list is collapsed no
    matter how deeply nested (e.g. under `sessionDefaults.commandRules`)."""
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key in COMPACT_LIST_KEYS and isinstance(value, list):
                result[key] = [_OneLine(element) for element in value]
            else:
                result[key] = _wrap_compact_list_elements(value)
        return result
    if isinstance(node, list):
        return [_wrap_compact_list_elements(element) for element in node]
    return node


class _ConfigJSONEncoder(json.JSONEncoder):
    """Pretty-printing encoder (via `indent`) that additionally collapses any `_OneLine`-wrapped
    value onto a single line. Each such value is emitted as a unique placeholder string during the
    normal indented encode, then swapped for its compact one-line serialization in a final pass —
    a self-contained way to get per-node formatting that the stdlib `json` module otherwise can't
    express. The placeholder carries a per-instance random token so it can't collide with real
    string data in the document."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._token = secrets.token_hex(8)
        self._compact_blobs: list[str] = []
        self._placeholder_re = re.compile(rf'"@@klorb-oneline:{self._token}:(\d+)@@"')

    def default(self, o: Any) -> Any:
        if isinstance(o, _OneLine):
            index = len(self._compact_blobs)
            self._compact_blobs.append(json.dumps(o.value, sort_keys=self.sort_keys))
            return f"@@klorb-oneline:{self._token}:{index}@@"
        return super().default(o)

    def encode(self, o: Any) -> str:
        indented = super().encode(o)
        return self._placeholder_re.sub(
            lambda match: self._compact_blobs[int(match.group(1))], indented)


JSON_ERROR_CONTEXT_LINES = 2
"""Number of source lines shown before and after the offending line in the snippet
`_format_json_error_context` builds for a `json.JSONDecodeError` — enough to orient a user
skimming a hand-edited config file without dumping the whole thing."""


class SchemaInfo(BaseModel):
    """Identifies a persisted JSON file's type (`name`) and format (`version`)."""

    name: str
    version: str


def _format_json_error_context(text: str, exc: json.JSONDecodeError) -> str:
    """Render a small excerpt of `text` around `exc.lineno` (1-indexed, per the `json` module),
    with a `^` caret under `exc.colno`, for a human reading the parse error rather than staring
    at a bare byte offset. Used to build both the log line (`parse_versioned_json`) and the
    user-visible history notice (see `klorb.process_config.ProcessConfig.config_warnings`).
    """
    lines = text.splitlines()
    first = max(exc.lineno - 1 - JSON_ERROR_CONTEXT_LINES, 0)
    last = min(exc.lineno + JSON_ERROR_CONTEXT_LINES, len(lines))
    excerpt_lines: list[str] = []
    for lineno in range(first + 1, last + 1):
        excerpt_lines.append(f"{lineno:>5} | {lines[lineno - 1]}")
        if lineno == exc.lineno:
            excerpt_lines.append(f"      | {' ' * (exc.colno - 1)}^")
    return "\n".join(excerpt_lines)


def parse_versioned_json(
    text: str, *, expected_schema_name: str, source: str, warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Parse already-read `text` as a schema-enveloped JSON document (see module docstring),
    validating and stripping its `schema` block exactly like `read_versioned_json` does for an
    on-disk file — this is what that function delegates to once it has the file's contents in
    hand, and it's also what a caller reading a *packaged* resource via `importlib.resources`
    (which yields text, not a `Path`) should use directly, e.g.
    `klorb.process_config`'s built-in-defaults layer. `source` identifies `text`'s origin
    (a `Path`, or a resource name) for the log messages below only.

    Text that isn't valid JSON at all (a hand-edited config file with a typo, a torn write from
    a crashed process, etc.) is treated the same as a schema-name mismatch: an error is logged
    naming `source`, the parse exception, and a `_format_json_error_context` excerpt of the
    surrounding lines, and `{}` is returned rather than letting `json.JSONDecodeError`
    propagate — one malformed layer must not take down the whole process, since every caller of
    this helper merges several independently-sourced layers (see
    `klorb.process_config.load_process_config`) where the rest are still worth loading. If
    `warnings` is given, the same human-readable message is appended to it so a caller can
    surface it somewhere a user will actually see it (a log line alone is easy to miss) — see
    `ProcessConfig.config_warnings`.
    """
    try:
        contents: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as exc:
        message = (
            f"{source} is not valid JSON ({exc.msg} at line {exc.lineno}, column {exc.colno}); "
            f"ignoring its contents.\n{_format_json_error_context(text, exc)}"
        )
        logger.error(message)
        if warnings is not None:
            warnings.append(message)
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


def read_versioned_json(
    path: Path, *, expected_schema_name: str, warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Read a schema-enveloped JSON file's data, or `{}` if `path` doesn't exist.

    The `schema` key, if present, is validated against `expected_schema_name` and then
    stripped; a name mismatch logs a warning and discards the rest of the file's contents,
    since it's probably the wrong file type. A file with no `schema` block at all is still
    accepted, with its keys returned as-is, since files like `klorb-config.json` are
    hand-authored and may omit it; this is logged at debug level rather than treated as an
    error. A file that isn't valid JSON at all logs an error and is likewise discarded (`{}`)
    rather than raising — see `parse_versioned_json`, which this delegates to, including for
    what `warnings` (if given) collects.
    """
    if not path.is_file():
        logger.debug("No file at %s; skipping.", path)
        return {}

    return parse_versioned_json(
        path.read_text(encoding="utf-8"), expected_schema_name=expected_schema_name,
        source=str(path), warnings=warnings)


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

    The document is pretty-printed with two-space indentation, except that the permission-rule
    lists keyed by `COMPACT_LIST_KEYS` (`allow`/`ask`/`deny`) get each of their elements collapsed
    onto a single line (see `_ConfigJSONEncoder`) so an argv token pattern like
    `["python", "-m", "pytest", "**"]` stays readable on one line instead of one token per line.
    """
    if SCHEMA_KEY in data:
        raise ValueError(f"data already contains a {SCHEMA_KEY!r} key; refusing to overwrite it")

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {SCHEMA_KEY: {"name": schema_name, "version": schema_version}, **data}

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(json.dumps(_wrap_compact_list_elements(payload), indent=2, cls=_ConfigJSONEncoder))
            tmp_file.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise
    logger.debug("Wrote %s (schema %s v%s).", path, schema_name, schema_version)
