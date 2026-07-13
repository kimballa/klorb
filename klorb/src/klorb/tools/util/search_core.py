# © Copyright 2026 Aaron Kimball
"""The subject-agnostic core shared by the line-search tools (`GrepTool`, `SearchScratchpadTool`,
`SearchMemoriesTool`): validating a `queries` array, compiling it into a single alternation
pattern, finding the line indices that match, and rendering matches (with optional surrounding
context) into a compact, token-dense line format.

The dense line format is one string per reported line: a leading `*` when the line itself matched
a query, or a space when it's only surrounding context, followed by the 1-based line number, a
`|`, and the line's text — e.g. `"*42|def hello():"` or `" 41|import os"`. Because every line
already carries both its match marker and its own line number, callers report a flat list of
these strings per searched file with no enclosing `start_line`/`end_line` block structure: where
two merged context windows within one file aren't contiguous, the jump in the embedded line
numbers is what marks the gap (`context_lines_for_matches` emits no separator between windows).
Splitting a dense line back apart is unambiguous — the line number never contains a `|`, so
splitting on the first `|` always recovers the number and the (possibly `|`-containing) text.
"""

import re
from typing import Any


def validate_queries(queries: Any) -> list[str]:
    """Validate a model-supplied `queries` argument, returning it unchanged as a `list[str]`,
    or raising `ValueError` if it isn't a non-empty list of strings. Shared so every line-search
    tool rejects the same malformed inputs with the same messages.
    """
    if not isinstance(queries, list) or not queries:
        raise ValueError("queries must be a non-empty array of search strings")
    for query in queries:
        if not isinstance(query, str):
            raise ValueError(f"each entry in queries must be a string, got {query!r}")
    return queries


def compile_queries(
    queries: list[str], *, is_regex: bool = False, case_insensitive: bool = False,
) -> re.Pattern[str]:
    """Compile `queries` into a single pattern that matches a line hitting any one of them,
    equivalent to `grep -e query1 -e query2 ...`. Each query is a literal substring (via
    `re.escape`) unless `is_regex`, in which case each is its own Python regular expression
    wrapped in a non-capturing group so alternation binds correctly. Raises `ValueError` if a
    regex query fails to compile.
    """
    flags = re.IGNORECASE if case_insensitive else 0
    if is_regex:
        combined_pattern = "|".join(f"(?:{query})" for query in queries)
    else:
        combined_pattern = "|".join(re.escape(query) for query in queries)
    try:
        return re.compile(combined_pattern, flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex in {queries!r}: {exc}") from exc


def match_line_indices(all_lines: list[str], compiled: re.Pattern[str]) -> list[int]:
    """Return the ascending 0-based indices of the lines in `all_lines` that `compiled` matches."""
    return [index for index, line in enumerate(all_lines) if compiled.search(line)]


def format_match_line(line_number: int, text: str, *, matched: bool) -> str:
    """Render one result line in the dense format: a leading `*` if the line itself matched a
    query (else a space), then the 1-based `line_number`, a `|`, and the line's `text`.
    """
    return ("*" if matched else " ") + f"{line_number}|{text}"


def context_lines_for_matches(
    all_lines: list[str], matched_indices: list[int], context_lines: int,
) -> list[str]:
    """Return a flat list of dense-format result lines (see `format_match_line`) covering every
    index in `matched_indices` plus `context_lines` lines of surrounding context on each side,
    with overlapping or adjacent windows merged (as `grep -C` collapses them). `matched_indices`
    must be ascending. Non-contiguous windows are simply concatenated; the jump in the embedded
    1-based line numbers marks the gap, so no separator line is emitted between them.
    """
    total_lines = len(all_lines)
    windows: list[list[int]] = []
    for index in matched_indices:
        start = max(0, index - context_lines)
        end = min(total_lines - 1, index + context_lines)
        if windows and start <= windows[-1][1] + 1:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])

    matched_index_set = set(matched_indices)
    result: list[str] = []
    for start, end in windows:
        for i in range(start, end + 1):
            result.append(format_match_line(i + 1, all_lines[i], matched=i in matched_index_set))
    return result
