# © Copyright 2026 Aaron Kimball
"""A Tool that recursively searches a directory tree for lines matching any of several literal
strings or regular expressions, returning each match with surrounding context lines."""

import fnmatch
import logging
from pathlib import Path
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_read
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.util import (
    WalkReport,
    compile_queries,
    context_lines_for_matches,
    match_line_indices,
    matches_only,
    validate_output_style,
    validate_queries,
    walk_readable_tree,
)

logger = logging.getLogger(__name__)

_GITIGNORE_HIDDEN_NOTE = (
    "Some files were skipped without being searched because a .gitignore rule excludes them. "
    "Re-call Grep with use_gitignore=false to search gitignored files too.")


class GrepTool(Tool):
    """Recursively searches a directory tree for lines matching any of `queries` (each matched
    as a literal substring by default, or as a distinct Python regex when `is_regex` is true —
    a line matching any one of them counts as a hit, equivalent to `grep -e 'seq1' -e 'seq2'
    ...`), reusing `klorb.tools.util.walk_readable_tree` so the walk obeys `readDirs` at
    every directory level, not just at the given path itself — see that function's docstring for how
    a denied, ask-gated, or symlinked subdirectory is pruned rather than aborting the whole
    search.

    Each hit is reported with `context.process_config.grep_context_lines` lines of surrounding
    context on each side (like `grep -C`); overlapping or adjacent context windows within the
    same file are merged rather than reported as separately-overlapping results. Every matching
    file appears once in `result["files"]` as `{"filename", "lines"}`, where `lines` is a flat
    list of dense-format strings (see `klorb.tools.util.search_core`) — a leading `*` marks a
    line that itself matched, and each line carries its own 1-based number, so a gap between two
    merged windows shows up as a jump in those numbers with no separator line.

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
            "filename glob (e.g. '*.py'). path empty means search the whole project root. "
            f"Each hit is returned with {self._context_lines} lines of surrounding context on "
            f"each side, like `grep -C {self._context_lines}`, merging overlapping or adjacent "
            "context windows within the same file. Results are grouped by file under 'files'; "
            "each line is a string like '*42|matched text' or ' 41|context text', where the "
            "leading '*' marks a matching line and the number is its 1-based line number (a gap "
            "in those numbers marks a break between context windows). Returns at most "
            f"{self._max_results} matching lines per call; a 'truncated' flag in the result "
            "means more matches exist than were returned. A subdirectory your readDirs "
            "permissions deny, or that "
            "requires confirmation, is silently skipped rather than failing the whole search "
            "— only the path itself raises if it isn't allowed. Files excluded by the project's "
            ".gitignore rules are skipped without being searched by default; when that happens "
            "the result sets 'gitignored_hidden' to true and includes a 'note', and you can "
            "re-call with use_gitignore=false to search gitignored files too. "
            "Use outputStyle to control the level of detail: \"ListFiles\" returns just "
            "deduplicated filenames, \"Matches\" returns only the hit lines (default), and "
            "\"FullContext\" returns hit lines plus surrounding context."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File or directory to search, relative to the project root unless "
                        "absolute. An empty string or null means the whole project root. "
                        "If a file, only that file is searched; if a directory, the search "
                        "is recursive."
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
                "outputStyle": {
                    "type": "string",
                    "description": (
                        "Controls the level of detail in results: \"ListFiles\" returns just "
                        "deduplicated filenames; \"Matches\" returns only the hit lines "
                        "(default); \"FullContext\" returns hit lines plus surrounding context."
                    ),
                },
                "use_gitignore": {
                    "type": "boolean",
                    "description": (
                        "Skip files and directories excluded by the project's .gitignore "
                        "rules. Defaults to true; set false to search gitignored files too. "
                        "Ignored only when path names a single file to search."
                    ),
                },
            },
            "required": ["queries"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        # None or empty-string default searches recursively from ${workspaceRoot}.
        search_path = args.get("path") or ""
        try:
            queries = validate_queries(args["queries"])
        except KeyError:
            raise ValueError(
                "Missing required argument: 'queries'. Provide a non-empty array of search strings.")
        is_regex = args.get("is_regex", False)
        case_insensitive = args.get("case_insensitive", False)
        file_glob = args.get("file_glob")
        use_gitignore = args.get("use_gitignore", True)
        output_style = validate_output_style(args.get("outputStyle"))
        logger.debug(
            "Grep %r in %r (is_regex=%s, case_insensitive=%s, file_glob=%s, use_gitignore=%s, "
            "outputStyle=%s)",
            queries, search_path, is_regex, case_insensitive, file_glob, use_gitignore,
            output_style)

        compiled = compile_queries(
            queries, is_regex=is_regex, case_insensitive=case_insensitive)

        # For ListFiles mode, we collect just filenames (deduplicated strings).
        list_files: list[str] = []
        # For Matches/FullContext modes, we collect file dicts with lines.
        files: list[dict[str, Any]] = []
        match_count = 0
        truncated = False
        root_path: Path | None = None
        report = WalkReport()

        # Determine whether search_path is a single file or a directory tree.
        single_file: Path | None = None
        if search_path:
            resolved, verdict = resolve_and_evaluate_read(self.context, search_path)
            raise_if_not_allowed(
                verdict, resource_description=f"search {resolved}",
                path=resolved, is_write=False)
            if resolved.is_file():
                single_file = resolved
                root_path = resolved.parent
            elif not resolved.is_dir():
                raise FileNotFoundError(f"{search_path!r} does not exist")

        if single_file is not None:
            # Search just the one file.
            try:
                text = single_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                pass  # Skip unreadable files silently.
            else:
                all_lines = text.splitlines()
                matched_indices = match_line_indices(all_lines, compiled)
                if matched_indices:
                    if match_count + len(matched_indices) > self._max_results:
                        matched_indices = matched_indices[: self._max_results - match_count]
                        truncated = True
                    if output_style == "ListFiles":
                        list_files.append(str(single_file))
                    elif output_style == "Matches":
                        files.append({
                            "filename": str(single_file),
                            "lines": matches_only(all_lines, matched_indices),
                        })
                    else:  # FullContext
                        files.append({
                            "filename": str(single_file),
                            "lines": context_lines_for_matches(
                                all_lines, matched_indices, self._context_lines),
                        })
                    match_count += len(matched_indices)
        else:
            # Directory tree search (existing recursive walk).
            for dir_path, _subdirs, filenames in walk_readable_tree(
                    self.context, search_path, use_gitignore=use_gitignore, report=report):
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
                    matched_indices = match_line_indices(all_lines, compiled)
                    if not matched_indices:
                        continue

                    if match_count + len(matched_indices) > self._max_results:
                        matched_indices = matched_indices[: self._max_results - match_count]
                        truncated = True

                    if output_style == "ListFiles":
                        fname = str(file_path)
                        if fname not in list_files:
                            list_files.append(fname)
                    elif output_style == "Matches":
                        files.append({
                            "filename": str(file_path),
                            "lines": matches_only(all_lines, matched_indices),
                        })
                    else:  # FullContext
                        files.append({
                            "filename": str(file_path),
                            "lines": context_lines_for_matches(
                                all_lines, matched_indices, self._context_lines),
                        })
                    match_count += len(matched_indices)
                    if truncated:
                        break

        logger.debug(
            "Grep found %d match(es) in %d file(s) (truncated=%s)",
            match_count, len(files) if output_style != "ListFiles" else len(list_files), truncated)

        if output_style == "ListFiles":
            result: dict[str, Any] = {
                "root": str(root_path) if root_path is not None else search_path,
                "queries": queries,
                "is_regex": is_regex,
                "case_insensitive": case_insensitive,
                "file_glob": file_glob,
                "use_gitignore": use_gitignore,
                "files": list_files,
                "match_count": match_count,
                "truncated": truncated,
                "gitignored_hidden": report.gitignored_hidden,
            }
        else:
            result = {
                "root": str(root_path) if root_path is not None else search_path,
                "queries": queries,
                "is_regex": is_regex,
                "case_insensitive": case_insensitive,
                "file_glob": file_glob,
                "use_gitignore": use_gitignore,
                "context_lines": self._context_lines if output_style == "FullContext" else 0,
                "files": files,
                "match_count": match_count,
                "truncated": truncated,
                "gitignored_hidden": report.gitignored_hidden,
            }
        if report.gitignored_hidden:
            result["note"] = _GITIGNORE_HIDDEN_NOTE
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        queries = args.get("queries", "?")
        if error is not None:
            return f"Grep: {queries!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Grep: {queries!r}"
        count = result.get("match_count", 0)
        suffix = "+" if result.get("truncated") else ""
        plural = "es" if count != 1 else ""
        root = result.get("root", args.get("path", "?"))
        return f"Grep: {queries!r} in {root} ({count}{suffix} match{plural})"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["files"]` capped to its
        first 20 entries — a full grep result can span up to `self._max_results` (100 by
        default) matching lines across many files, far more than useful to show inline.
        """
        if error is not None or not isinstance(result, dict) or "files" not in result:
            return super().detail_view(args, result, error)
        files = result["files"]
        if len(files) <= 20:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["files"] = files[:20]
        capped_result["files_omitted"] = len(files) - 20
        return super().detail_view(args, capped_result, error)
