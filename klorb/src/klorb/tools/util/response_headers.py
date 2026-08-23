# © Copyright 2026 Aaron Kimball
"""Shared `key: value` header-line rendering for a `Tool.format_response()` override."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def format_header_value(value: Any) -> str:
    """Render one header line's value: lowercase `true`/`false` for a bool, `str()` otherwise."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def format_header_lines(
    result: dict[str, Any], key_order: tuple[str, ...], *,
    known_elsewhere: frozenset[str] = frozenset(),
) -> list[str]:
    """Render `result`'s `key: value` header lines in `key_order`, skipping absent keys and
    `None` values. A key present in `result` but missing from both `key_order` and
    `known_elsewhere` is still emitted, appended at the end, with a `logger.warning()` call."""
    lines = [
        f"{key}: {format_header_value(result[key])}"
        for key in key_order if key in result and result[key] is not None
    ]
    known = set(key_order) | known_elsewhere
    for key, value in result.items():
        if key in known or value is None:
            continue
        logger.warning(
            "format_header_lines: key %r present in result but missing from key_order/"
            "known_elsewhere; emitting at the end", key)
        lines.append(f"{key}: {format_header_value(value)}")
    return lines
