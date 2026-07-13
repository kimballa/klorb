# © Copyright 2026 Aaron Kimball
"""A Tool that performs a literal or regex search-and-replace across a single text file."""

import logging
import re
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_write
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class ReplaceAllTool(Tool):
    """Replaces every occurrence of `search` in a single text file with `new_text`, either as
    a literal substring or (`is_regex=True`) a regex pattern supporting `\\1`-style
    backreferences in `new_text`.

    `filename` is checked against `writeFiles` (an exact-match carve-out, checked first — see
    `klorb.permissions.workspace.resolve_and_evaluate_write`) and otherwise confined to
    `SessionConfig.workspace.path` and further checked against `writeDirs` before any disk I/O.
    """

    def name(self) -> str:
        return "ReplaceAll"

    def description(self) -> str:
        return (
            "Replaces every occurrence of search in filename with new_text. By default "
            "search is matched as a literal substring; set is_regex to treat it as a Python "
            "regular expression, in which case new_text may use \\1-style backreferences. "
            "case_insensitive ignores case when matching. multiline makes ^ and $ match the "
            "start/end of each line rather than only the start/end of the whole file "
            "(only meaningful with is_regex). Returns replacements_made so you can sanity-check "
            "an unexpectedly large or zero match count."
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

        pattern = search if is_regex else re.escape(search)
        flags = re.IGNORECASE if case_insensitive else re.NOFLAG
        if multiline:
            flags |= re.MULTILINE

        new_content, replacements_made = re.subn(pattern, new_text, content, flags=flags)
        if replacements_made > 0:
            path.write_text(new_content, encoding="utf-8")

        logger.debug("ReplaceAll %s made %d replacement(s)", filename, replacements_made)

        return {
            "filename": filename,
            "replacements_made": replacements_made,
            "is_regex": is_regex,
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        filename = args.get("filename", "?")
        if error is not None:
            return f"Replace all: {filename} failed: {error}"
        if not isinstance(result, dict):
            return f"Replace all: {filename}"
        count = result.get("replacements_made")
        kind = "regex" if result.get("is_regex") else "literal"
        plural = "" if count == 1 else "s"
        return f"Replace all: {result.get('filename', filename)} ({count} {kind} replacement{plural})"
