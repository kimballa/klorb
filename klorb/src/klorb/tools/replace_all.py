# © Copyright 2026 Aaron Kimball
"""A Tool that performs a literal or regex search-and-replace across a single text file."""

import logging
import re
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_write
from klorb.tools.tool import NO_READFILE_VERIFICATION_NOTE, Tool
from klorb.tools.util import format_match_line
from klorb.tools.util.response_headers import format_header_lines

logger = logging.getLogger(__name__)

_REPLACE_ALL_RESULT_HEADER_ORDER = (
    "filename", "search", "is_regex", "num_replacements_made", "note",
)
"""Key order `ReplaceAllTool.format_response()` renders a result dict's header lines in."""


def _apply_replacements(
    content: str, pattern: re.Pattern[str], new_text: str,
) -> tuple[str, int, list[int]]:
    """Replace every non-overlapping match of `pattern` in `content` with `new_text`'s
    backreference expansion, returning the new content, the number of replacements made, and
    the ascending 1-based line numbers (in the new content) that any replacement touched."""
    parts: list[str] = []
    changed_lines: set[int] = set()
    current_line = 1
    pos = 0
    count = 0
    for match in pattern.finditer(content):
        prefix = content[pos:match.start()]
        parts.append(prefix)
        current_line += prefix.count("\n")
        expansion = match.expand(new_text)
        start_line = current_line
        parts.append(expansion)
        current_line += expansion.count("\n")
        changed_lines.update(range(start_line, current_line + 1))
        pos = match.end()
        count += 1
    parts.append(content[pos:])
    return "".join(parts), count, sorted(changed_lines)


class ReplaceAllTool(Tool):
    """Replaces every occurrence of `search` in a single text file with `new_text`, either as
    a literal substring or (`is_regex=True`) a regex pattern supporting `\\1`-style
    backreferences in `new_text`.

    `filename` is checked against `writeFiles` and otherwise confined to
    `SessionConfig.workspace.path` and further checked against `writeDirs` before any disk I/O.
    """

    def name(self) -> str:
        return "ReplaceAll"

    def category(self) -> str:
        return "FILES"

    def is_read_only(self) -> bool:
        return False

    def default_described(self) -> bool:
        return False

    def description(self) -> str:
        return (
            "Replaces every occurrence of search in filename with new_text. "
            "To match 'foo', 'Foo', and 'FOO' all at once, set case_insensitive=true. "
            "By default search is matched as a literal substring; set is_regex to treat it as "
            "a Python regular expression, in which case new_text may use \\1-style backreferences. "
            "multiline makes ^ and $ match the "
            "start/end of each line rather than only the start/end of the whole file "
            "(only meaningful with is_regex). Returns num_replacements_made so you can "
            "sanity-check an unexpectedly large or zero match count, plus every post-replacement "
            "line that changed — no follow-up ReadFile is needed to verify."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Path to the text file to edit.",
                },
                "search": {
                    "type": "string",
                    "description": "Literal text (default) or regex pattern (if is_regex) to find.",
                },
                "new_text": {
                    "type": "string",
                    "description": "Replacement text. May use \\1-style backreferences when is_regex.",
                },
                "is_regex": {
                    "type": "boolean",
                    "description": "Treat search as a regular expression. Defaults to false.",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Match search case-insensitively. Defaults to false.",
                },
                "multiline": {
                    "type": "boolean",
                    "description": (
                        "Make ^ and $ match line boundaries rather than only the start/end "
                        "of the file. Only meaningful with is_regex. Defaults to false."
                    ),
                },
            },
            "required": ["filename", "search", "new_text"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            filename = args["filename"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'filename'. Provide the path of the file to edit.")
        try:
            search = args["search"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'search'. Provide the text (or regex) to search for.")
        try:
            new_text = args["new_text"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'new_text'. Provide the replacement text.")
        is_regex = args.get("is_regex", False)
        case_insensitive = args.get("case_insensitive", False)
        multiline = args.get("multiline", False)
        logger.debug(
            "ReplaceAll %s (is_regex=%s, case_insensitive=%s, multiline=%s)",
            filename, is_regex, case_insensitive, multiline,
        )

        path, verdict = resolve_and_evaluate_write(self.context, filename)
        raise_if_not_allowed(
            verdict, resource_description=f"write to {path}", path=path, is_write=True)

        content = path.read_text(encoding="utf-8")

        pattern_text = search if is_regex else re.escape(search)
        flags = re.IGNORECASE if case_insensitive else re.NOFLAG
        if multiline:
            flags |= re.MULTILINE
        pattern = re.compile(pattern_text, flags)

        new_content, num_replacements_made, changed_lines = _apply_replacements(
            content, pattern, new_text)
        if num_replacements_made > 0:
            path.write_text(new_content, encoding="utf-8")
        if self.context.session is not None:
            self.context.session.file_accessed(
                str(path), "write" if num_replacements_made > 0 else "read")

        logger.debug(
            "ReplaceAll %s made %d replacement(s) across %d line(s)",
            filename, num_replacements_made, len(changed_lines))

        new_lines = new_content.splitlines()
        lines = [format_match_line(n, new_lines[n - 1], matched=True) for n in changed_lines]

        return {
            "filename": filename,
            "search": search,
            "is_regex": is_regex,
            "num_replacements_made": num_replacements_made,
            "note": NO_READFILE_VERIFICATION_NOTE,
            "lines": lines,
        }

    def format_response(self, apply_output: Any) -> str:
        """Render `apply_output` as `key: value` header lines in
        `_REPLACE_ALL_RESULT_HEADER_ORDER`, followed by the filename and its already-prefixed
        `*N|text` changed lines, in the same plain-text block format as `GrepTool`."""
        header_lines = format_header_lines(
            apply_output, _REPLACE_ALL_RESULT_HEADER_ORDER, known_elsewhere=frozenset({"lines"}))
        lines = apply_output.get("lines")
        if not lines:
            return "\n".join(header_lines)
        body = "\n".join([apply_output["filename"], *lines])
        return "\n".join(header_lines) + "\n\n" + body

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        filename = args.get("filename", "?")
        if error is not None:
            return f"Replace all: {filename} failed: {error}"
        if not isinstance(result, dict):
            return f"Replace all: {filename}"
        count = result.get("num_replacements_made")
        kind = "regex" if result.get("is_regex") else "literal"
        plural = "" if count == 1 else "s"
        return f"Replace all: {result.get('filename', filename)} ({count} {kind} replacement{plural})"
