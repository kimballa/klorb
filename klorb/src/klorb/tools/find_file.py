# © Copyright 2026 Aaron Kimball
"""A Tool that recursively finds files in a directory tree whose bare name matches a glob."""

import fnmatch
import logging
from pathlib import Path
from typing import Any

from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.util import WalkReport, walk_readable_tree

logger = logging.getLogger(__name__)

_GITIGNORE_HIDDEN_NOTE = (
    "Some matches were hidden because a .gitignore rule excludes them. Re-call FindFile with "
    "use_gitignore=false to search gitignored files too.")


class FindFileTool(Tool):
    """Recursively searches a directory tree for files whose bare name (not full path) matches
    a glob `pattern` (e.g. `*.py` or `*_context*`), reusing
    `klorb.tools.util.walk_readable_tree` so the walk obeys `readDirs` at every directory
    level, not just at `dirname` itself — see that function's docstring for how a denied,
    ask-gated, or symlinked subdirectory is pruned rather than aborting the whole search.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._max_results = context.process_config.find_file_max_results

    def name(self) -> str:
        return "FindFile"

    def description(self) -> str:
        return (
            "Recursively finds files in a directory tree whose bare name (not full path) "
            "matches a glob pattern, e.g. '*.py' or '*_context*', so you can locate a file "
            "without knowing exactly where it lives. dirname is optional, defaulting to the whole "
            f"project root. Returns at most {self._max_results} matches per call; a "
            "'truncated' flag in the result means more matches exist than were returned. A "
            "subdirectory your readDirs permissions deny, or that requires confirmation, is "
            "silently skipped rather than failing the whole search — only dirname itself "
            "raises if it isn't allowed. Files excluded by the project's .gitignore rules are "
            "skipped by default; when that happens the result sets 'gitignored_hidden' to true "
            "and includes a 'note', and you can re-call with use_gitignore=false to search "
            "gitignored files too."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "dirname": {
                    "type": "string",
                    "description": (
                        "Directory to search, relative to the project root unless absolute. "
                        "An empty string or null means the whole project root."
                    ),
                },
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern matched against each file's bare name, e.g. '*.py' or "
                        "'*_context*'. A pattern with no wildcard matches only an exact name."
                    ),
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Match pattern case-insensitively. Defaults to false.",
                },
                "use_gitignore": {
                    "type": "boolean",
                    "description": (
                        "Skip files and directories excluded by the project's .gitignore "
                        "rules. Defaults to true; set false to search gitignored files too."
                    ),
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        dirname = args.get("dirname", "")  # Empty-string default searches recursively from ${workspaceRoot}.
        try:
            pattern = args["pattern"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'pattern'. Provide the filename glob pattern to match.")
        case_insensitive = args.get("case_insensitive", False)
        use_gitignore = args.get("use_gitignore", True)
        logger.debug(
            "FindFile %r in %r (case_insensitive=%s, use_gitignore=%s)",
            pattern, dirname, case_insensitive, use_gitignore)

        match_pattern = pattern.lower() if case_insensitive else pattern

        matches: list[str] = []
        truncated = False
        root_path: Path | None = None
        report = WalkReport()
        for dir_path, _subdirs, filenames in walk_readable_tree(
                self.context, dirname, use_gitignore=use_gitignore, report=report):
            if root_path is None:
                root_path = dir_path
            if truncated:
                break
            for filename in filenames:
                candidate = filename.lower() if case_insensitive else filename
                if not fnmatch.fnmatch(candidate, match_pattern):
                    continue
                if len(matches) >= self._max_results:
                    truncated = True
                    break
                matches.append(str(dir_path / filename))

        logger.debug("FindFile found %d match(es) (truncated=%s)", len(matches), truncated)

        result: dict[str, Any] = {
            "root": str(root_path) if root_path is not None else dirname,
            "pattern": pattern,
            "case_insensitive": case_insensitive,
            "use_gitignore": use_gitignore,
            "matches": matches,
            "truncated": truncated,
            "gitignored_hidden": report.gitignored_hidden,
        }
        if report.gitignored_hidden:
            result["note"] = _GITIGNORE_HIDDEN_NOTE
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        pattern = args.get("pattern", "?")
        if error is not None:
            return f"Find file: {pattern!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Find file: {pattern!r}"
        count = len(result.get("matches", []))
        suffix = "+" if result.get("truncated") else ""
        plural = "es" if count != 1 else ""
        root = result.get("root", args.get("dirname", "?"))
        return f"Find file: {pattern!r} in {root} ({count}{suffix} match{plural})"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["matches"]` capped to its
        first 20 entries — a full result can hold up to `self._max_results` (100 by default)
        matches, far more than useful to show inline.
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
