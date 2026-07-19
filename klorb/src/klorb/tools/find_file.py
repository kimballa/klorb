# © Copyright 2026 Aaron Kimball
"""A Tool that recursively finds files in a directory tree whose bare name matches a glob."""

import fnmatch
import logging
from pathlib import Path
from typing import Any

from klorb.tools.interruptible_tool import InterruptibleTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.util import walk_readable_tree

logger = logging.getLogger(__name__)

_GITIGNORE_HIDDEN_NOTE = (
    "Some matches were hidden because a .gitignore rule excludes them. Re-call FindFile with "
    "use_gitignore=false to search gitignored files too.")


class FindFileTool(InterruptibleTool):
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

    def category(self) -> str:
        return "FILES"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Recursively finds files in a directory tree whose bare name (not full path) "
            "matches a glob pattern, e.g. '*.py' or '*_context*', so you can locate a file "
            "without knowing exactly where it lives. dirname is optional, defaulting to the whole "
            f"project root. Returns at most {self._max_results} matches per call; a "
            "'truncated' flag in the result means more matches exist than were returned. A "
            "subdirectory your readDirs permissions deny, or that requires confirmation, is "
            "silently skipped rather than failing the whole search — only dirname itself "
            "raises if it isn't allowed. Matches whose file is excluded by the project's "
            ".gitignore rules are not listed by default; when a gitignored file would have "
            "matched, the result sets 'gitignored_hidden' to true and includes a 'note', and "
            "you can re-call with use_gitignore=false to list gitignored matches too."
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
        cancelled = False
        root_path: Path | None = None
        # Set true only once a file that *actually matches the glob* is found among the gitignored
        # entries — not merely because some gitignored entry exists — so the "hidden matches" note
        # never fires on a search whose pattern matches nothing that gitignore excluded.
        gitignored_match_found = False
        # Polled between directories so a Ctrl+C/Escape interrupt stops a search over a large tree
        # promptly, returning whatever matches were found so far — see `InterruptibleTool`.
        cancel_event = self._active_cancel_event()
        for dir_path, _subdirs, filenames, gitignored_filenames in walk_readable_tree(
                self.context, dirname, use_gitignore=use_gitignore):
            if root_path is None:
                root_path = dir_path
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
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
            if not gitignored_match_found:
                for filename in gitignored_filenames:
                    candidate = filename.lower() if case_insensitive else filename
                    if fnmatch.fnmatch(candidate, match_pattern):
                        gitignored_match_found = True
                        break

        logger.debug(
            "FindFile found %d match(es) (truncated=%s, cancelled=%s)",
            len(matches), truncated, cancelled)

        result: dict[str, Any] = {
            "root": str(root_path) if root_path is not None else dirname,
            "pattern": pattern,
            "case_insensitive": case_insensitive,
            "use_gitignore": use_gitignore,
            "matches": matches,
            "truncated": truncated,
            "cancelled": cancelled,
            "gitignored_hidden": gitignored_match_found,
        }
        if gitignored_match_found:
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
        cancelled = " — interrupted" if result.get("cancelled") else ""
        return f"Find file: {pattern!r} in {root} ({count}{suffix} match{plural}{cancelled})"

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
