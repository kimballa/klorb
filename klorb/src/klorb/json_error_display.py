# © Copyright 2026 Aaron Kimball
"""Renders a legible excerpt of a JSON parse error for a human or a model to read.
"""

import json

JSON_ERROR_CONTEXT_LINES = 2
"""Number of source lines shown before and after the offending line in the excerpt
`format_json_error_context` builds — enough to orient a reader without dumping the whole
document."""

_MAX_LINE_WIDTH = 100
"""Longest a single excerpt line is shown without windowing/truncation — long lines are common
in a tool call's raw `arguments` string (typically compact, single-line JSON), and dumping one
in full would bury the caret in noise."""


def format_json_error_context(
    text: str, exc: json.JSONDecodeError, *, context_lines: int = JSON_ERROR_CONTEXT_LINES,
) -> str:
    """Render a small, line-numbered excerpt of `text` around `exc.lineno`/`exc.colno`
    (1-indexed, per the `json` module), with a `^` caret directly beneath `exc.colno` on the
    offending line.

    The caret always lands under the line it names, not after the whole excerpt — the excerpt
    is built line-by-line, so this holds even when `text` spans many lines (a tool call's
    `arguments` string routinely does, since embedded, unescaped newlines are themselves a
    common cause of the parse failure this is rendering). A line longer than `_MAX_LINE_WIDTH`
    is windowed around its relevant column rather than shown in full, so one very long line
    (a compact JSON blob is often entirely one line) doesn't bury the excerpt in noise; other,
    merely-long context lines are truncated with a trailing `...` instead, since exact alignment
    doesn't matter there.
    """
    lines = text.splitlines() or [""]
    error_lineno = min(max(exc.lineno, 1), len(lines))
    first = max(error_lineno - 1 - context_lines, 0)
    last = min(error_lineno + context_lines, len(lines))

    error_line = lines[error_lineno - 1]
    window_start = 0
    if len(error_line) > _MAX_LINE_WIDTH:
        half = _MAX_LINE_WIDTH // 2
        window_start = max(0, min(exc.colno - 1 - half, len(error_line) - _MAX_LINE_WIDTH))
    windowed_error_line = error_line[window_start:window_start + _MAX_LINE_WIDTH]
    prefix = "..." if window_start > 0 else ""
    suffix = "..." if window_start + _MAX_LINE_WIDTH < len(error_line) else ""

    excerpt_lines: list[str] = []
    for lineno in range(first + 1, last + 1):
        if lineno == error_lineno:
            excerpt_lines.append(f"{lineno:>5} | {prefix}{windowed_error_line}{suffix}")
            caret_column = len(prefix) + (exc.colno - 1 - window_start)
            excerpt_lines.append(f"      | {' ' * caret_column}^")
        else:
            line_text = lines[lineno - 1]
            if len(line_text) > _MAX_LINE_WIDTH:
                line_text = line_text[:_MAX_LINE_WIDTH] + "..."
            excerpt_lines.append(f"{lineno:>5} | {line_text}")
    return "\n".join(excerpt_lines)
