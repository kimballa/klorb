# © Copyright 2026 Aaron Kimball
"""A Tool that recursively searches a directory tree for lines matching a literal string or a
regular expression."""

import fnmatch
import logging
import re
from pathlib import Path
from typing import Any

from klorb.tools.dir_walk import walk_readable_tree
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class GrepTool(Tool):
    """Recursively searches a directory tree for lines matching `pattern` (a literal substring
    by default, or a Python regex when `is_regex` is true), reusing
    `klorb.tools.dir_walk.walk_readable_tree` so the walk obeys `readDirs` at every directory
    level, not just at `dirname` itself — see that function's docstring for how a denied,
    ask-gated, or symlinked subdirectory is pruned rather than aborting the whole search.

    A file that can't be decoded as UTF-8 text, or can't be opened at all, is skipped silently
    (treated as binary) rather than raising — matches are only ever reported from files
    readable as text, matching common `grep -I` behavior.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._max_results = context.process_config.grep_max_results

    def name(self) -> str:
        return "Grep"

    def description(self) -> str:
        return (
            "Recursively searches a directory tree for lines matching a pattern, so you can "
            "find where a piece of text or code appears without reading every file yourself. "
            "pattern is matched as a literal substring by default; set is_regex to treat it as "
            "a Python regular expression. Optionally filter which files are searched with a "
            "filename glob (e.g. '*.py'). dirname empty means search the whole project root. "
            f"Returns at most {self._max_results} matches per call; a 'truncated' flag in the "
            "result means more matches exist than were returned. A subdirectory your readDirs "
            "permissions deny, or that requires confirmation, is silently skipped rather than "
            "failing the whole search — only dirname itself raises if it isn't allowed."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "dirname": {
                    "type": "string",
                    "description": (
                        "Directory to search, relative to the project root unless absolute. "
                        "An empty string means the whole project root."
                    ),
                },
                "pattern": {
                    "type": "string",
                    "description": "Text to search for in each line, literal unless is_regex.",
                },
                "is_regex": {
                    "type": "boolean",
                    "description": "Treat pattern as a Python regular expression. Defaults to false.",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Match pattern case-insensitively. Defaults to false.",
                },
                "file_glob": {
                    "type": "string",
                    "description": (
                        "Optional filename glob (e.g. '*.py') restricting which files are "
                        "searched, matched against each file's bare name."
                    ),
                },
            },
            "required": ["dirname", "pattern"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        dirname = args["dirname"]
        pattern = args["pattern"]
        is_regex = args.get("is_regex", False)
        case_insensitive = args.get("case_insensitive", False)
        file_glob = args.get("file_glob")
        logger.debug(
            "Grep %r in %r (is_regex=%s, case_insensitive=%s, file_glob=%s)",
            pattern, dirname, is_regex, case_insensitive, file_glob)

        flags = re.IGNORECASE if case_insensitive else 0
        try:
            compiled = re.compile(pattern if is_regex else re.escape(pattern), flags)
        except re.error as exc:
            raise ValueError(f"Invalid regex {pattern!r}: {exc}") from exc

        matches: list[dict[str, Any]] = []
        truncated = False
        root_path: Path | None = None
        for dir_path, _subdirs, filenames in walk_readable_tree(self.context, dirname):
            if root_path is None:
                root_path = dir_path
            if truncated:
                break
            for filename in filenames:
                if file_glob and not fnmatch.fnmatch(filename, file_glob):
                    continue
                file_path = dir_path / filename
                try:
                    text = file_path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                for line_number, line in enumerate(text.splitlines(), start=1):
                    if not compiled.search(line):
                        continue
                    if len(matches) >= self._max_results:
                        truncated = True
                        break
                    matches.append({
                        "filename": str(file_path),
                        "line_number": line_number,
                        "line": line,
                    })
                if truncated:
                    break

        logger.debug("Grep found %d match(es) (truncated=%s)", len(matches), truncated)

        return {
            "root": str(root_path) if root_path is not None else dirname,
            "pattern": pattern,
            "is_regex": is_regex,
            "case_insensitive": case_insensitive,
            "file_glob": file_glob,
            "matches": matches,
            "truncated": truncated,
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        pattern = args.get("pattern", "?")
        if error is not None:
            return f"Grep: {pattern!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Grep: {pattern!r}"
        count = len(result.get("matches", []))
        suffix = "+" if result.get("truncated") else ""
        plural = "es" if count != 1 else ""
        root = result.get("root", args.get("dirname", "?"))
        return f"Grep: {pattern!r} in {root} ({count}{suffix} match{plural})"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["matches"]` capped to its
        first 20 entries — a full grep result can hold up to `self._max_results` (500 by
        default) matches, far more than useful to show inline.
        """
        if error is not None or not isinstance(result, dict) or "matches" not in result:
            return super().detail_view(args, result, error)
        matches = result["matches"]
        if len(matches) <= 20:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["matches"] = matches[:20]
        capped_result["matches_omitted"] = len(matches) - 20
        return super().detail_view(args, capped_result, error)
