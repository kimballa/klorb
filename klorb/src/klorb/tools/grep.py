# © Copyright 2026 Aaron Kimball
"""A Tool that recursively searches a directory tree for lines matching any of several literal
strings or regular expressions, returning each match with surrounding context lines."""

import fnmatch
import logging
import re
from pathlib import Path
from typing import Any

from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.util import walk_readable_tree

logger = logging.getLogger(__name__)


class GrepTool(Tool):
    """Recursively searches a directory tree for lines matching any of `queries` (each matched
    as a literal substring by default, or as a distinct Python regex when `is_regex` is true —
    a line matching any one of them counts as a hit, equivalent to `grep -e 'seq1' -e 'seq2'
    ...`), reusing `klorb.tools.util.walk_readable_tree` so the walk obeys `readDirs` at
    every directory level, not just at `dirname` itself — see that function's docstring for how
    a denied, ask-gated, or symlinked subdirectory is pruned rather than aborting the whole
    search.

    Each hit is reported with `context.process_config.grep_context_lines` lines of surrounding
    context on each side (like `grep -C`); overlapping or adjacent context windows within the
    same file are merged into a single block rather than reported as separately-overlapping
    results.

    A file that can't be decoded as UTF-8 text, or can't be opened at all, is skipped silently
    (treated as binary) rather than raising — matches are only ever reported from files
    readable as text, matching common `grep -I` behavior.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._max_results = context.process_config.grep_max_results
        self._context_lines = context.process_config.grep_context_lines

    def name(self) -> str:
        return "Grep"

    def description(self) -> str:
        return (
            "Recursively searches a directory tree for lines matching any of the given "
            "queries, so you can find where a piece of text or code appears without reading "
            "every file yourself. Each entry in queries is matched as a literal substring by "
            "default; set is_regex to treat every entry as a distinct Python regular "
            "expression instead. A line matching any one query counts as a hit — equivalent to "
            "`grep -e query1 -e query2 ...`. Optionally filter which files are searched with a "
            "filename glob (e.g. '*.py'). dirname empty means search the whole project root. "
            f"Each hit is returned with {self._context_lines} lines of surrounding context on "
            f"each side, like `grep -C {self._context_lines}`, merging overlapping or adjacent "
            f"context windows within the same file. Returns at most {self._max_results} "
            "matching lines per call; a 'truncated' flag in the result means more matches "
            "exist than were returned. A subdirectory your readDirs permissions deny, or that "
            "requires confirmation, is silently skipped rather than failing the whole search "
            "— only dirname itself raises if it isn't allowed."
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
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "One or more strings to search for in each line; a line matching any "
                        "one of them is returned. Each is a literal substring unless is_regex "
                        "is true, in which case each is its own Python regular expression."
                    ),
                },
                "is_regex": {
                    "type": "boolean",
                    "description": (
                        "Treat every entry in queries as a Python regular expression. "
                        "Defaults to false."
                    ),
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Match queries case-insensitively. Defaults to false.",
                },
                "file_glob": {
                    "type": "string",
                    "description": (
                        "Optional filename glob (e.g. '*.py') restricting which files are "
                        "searched, matched against each file's bare name."
                    ),
                },
            },
            "required": ["dirname", "queries"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            dirname = args["dirname"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'dirname'. Provide the directory to search under.")
        try:
            queries = args["queries"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'queries'. Provide a non-empty array of search strings.")
        if not isinstance(queries, list) or not queries:
            raise ValueError("queries must be a non-empty array of search strings")
        for query in queries:
            if not isinstance(query, str):
                raise ValueError(f"each entry in queries must be a string, got {query!r}")
        is_regex = args.get("is_regex", False)
        case_insensitive = args.get("case_insensitive", False)
        file_glob = args.get("file_glob")
        logger.debug(
            "Grep %r in %r (is_regex=%s, case_insensitive=%s, file_glob=%s)",
            queries, dirname, is_regex, case_insensitive, file_glob)

        flags = re.IGNORECASE if case_insensitive else 0
        if is_regex:
            combined_pattern = "|".join(f"(?:{query})" for query in queries)
        else:
            combined_pattern = "|".join(re.escape(query) for query in queries)
        try:
            compiled = re.compile(combined_pattern, flags)
        except re.error as exc:
            raise ValueError(f"Invalid regex in {queries!r}: {exc}") from exc

        blocks: list[dict[str, Any]] = []
        match_count = 0
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
                all_lines = text.splitlines()
                total_lines = len(all_lines)
                matched_indices = [
                    index for index, line in enumerate(all_lines) if compiled.search(line)
                ]
                if not matched_indices:
                    continue

                if match_count + len(matched_indices) > self._max_results:
                    matched_indices = matched_indices[: self._max_results - match_count]
                    truncated = True

                windows: list[list[int]] = []
                for index in matched_indices:
                    start = max(0, index - self._context_lines)
                    end = min(total_lines - 1, index + self._context_lines)
                    if windows and start <= windows[-1][1] + 1:
                        windows[-1][1] = max(windows[-1][1], end)
                    else:
                        windows.append([start, end])

                matched_index_set = set(matched_indices)
                for start, end in windows:
                    blocks.append({
                        "filename": str(file_path),
                        "start_line": start + 1,
                        "end_line": end + 1,
                        "lines": [
                            {
                                "line_number": i + 1,
                                "line": all_lines[i],
                                "matched": i in matched_index_set,
                            }
                            for i in range(start, end + 1)
                        ],
                    })
                match_count += len(matched_indices)
                if truncated:
                    break

        logger.debug(
            "Grep found %d match(es) in %d block(s) (truncated=%s)",
            match_count, len(blocks), truncated)

        return {
            "root": str(root_path) if root_path is not None else dirname,
            "queries": queries,
            "is_regex": is_regex,
            "case_insensitive": case_insensitive,
            "file_glob": file_glob,
            "context_lines": self._context_lines,
            "blocks": blocks,
            "match_count": match_count,
            "truncated": truncated,
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        queries = args.get("queries", "?")
        if error is not None:
            return f"Grep: {queries!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Grep: {queries!r}"
        count = result.get("match_count", 0)
        suffix = "+" if result.get("truncated") else ""
        plural = "es" if count != 1 else ""
        root = result.get("root", args.get("dirname", "?"))
        return f"Grep: {queries!r} in {root} ({count}{suffix} match{plural})"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["blocks"]` capped to its
        first 20 entries — a full grep result can hold up to `self._max_results` (500 by
        default) matching lines, far more than useful to show inline.
        """
        if error is not None or not isinstance(result, dict) or "blocks" not in result:
            return super().detail_view(args, result, error)
        blocks = result["blocks"]
        if len(blocks) <= 20:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["blocks"] = blocks[:20]
        capped_result["blocks_omitted"] = len(blocks) - 20
        return super().detail_view(args, capped_result, error)
