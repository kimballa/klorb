# © Copyright 2026 Aaron Kimball
"""A Tool that performs a literal or regex search-and-replace across a single text file."""

import logging
import re
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import evaluate_write, resolve_within_workspace
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class ReplaceAllTool(Tool):
    """Replaces every occurrence of `search` in a single text file with `new_text`, either as
    a literal substring or (`is_regex=True`) a regex pattern supporting `\\1`-style
    backreferences in `new_text`.

    `filename` is confined to `SessionConfig.workspace_root` and further checked against
    `writeDirs` (see `klorb.permissions.workspace.evaluate_write`) before any disk I/O.
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
        filename = args["filename"]
        search = args["search"]
        new_text = args["new_text"]
        is_regex = args.get("is_regex", False)
        case_insensitive = args.get("case_insensitive", False)
        multiline = args.get("multiline", False)
        logger.debug(
            "ReplaceAll %s (is_regex=%s, case_insensitive=%s, multiline=%s)",
            filename, is_regex, case_insensitive, multiline,
        )

        path = resolve_within_workspace(self.context, filename)
        raise_if_not_allowed(evaluate_write(self.context, path), resource_description=f"write to {path}")

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
