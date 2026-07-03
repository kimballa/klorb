# © Copyright 2026 Aaron Kimball
"""Minimal ANSI color helper for the eval harness's terminal output.

This module never inspects a stream itself — callers decide (via `use_color()`) whether color
is appropriate for the stream they're writing to, and pass that decision explicitly into
`colorize()`, so output piped to a file or CI log (not a real terminal) stays plain text.
"""

import sys
from typing import TextIO

_RESET = "\033[0m"
_CODES = {
    "red": "31",
    "green": "32",
    "yellow": "33",
    "cyan": "36",
    "bold": "1",
}


def use_color(stream: TextIO = sys.stdout) -> bool:
    """Whether `stream` looks like a real terminal, so ANSI color codes are safe to emit."""
    return stream.isatty()


def colorize(text: str, color: str, *, enabled: bool) -> str:
    """Wrap `text` in the ANSI code for `color` if `enabled`, else return it unchanged."""
    if not enabled:
        return text
    return f"\033[{_CODES[color]}m{text}{_RESET}"
