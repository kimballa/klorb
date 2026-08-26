# © Copyright 2026 Aaron Kimball
"""Recursively searches a directory tree for lines matching literal strings or regular expressions."""

import fnmatch
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_read
from klorb.tools.exceptions import ToolCallError
from klorb.tools.interruptible_tool import InterruptibleTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.util import (
    SpillDir,
    coerce_queries_arg,
    compile_queries,
    context_lines_for_matches,
    get_or_create_secret_redactor,
    match_line_indices,
    matches_only,
    validate_output_style,
    validate_queries,
    walk_readable_tree,
)
from klorb.tools.util.response_headers import format_header_lines

logger = logging.getLogger(__name__)

_GITIGNORE_HIDDEN_NOTE = (
    "Some files were skipped without being searched because a .gitignore rule excludes them. "
    "Re-call Grep with use_gitignore=false to search gitignored files too.")

_TRUNCATION_SUFFIX = "[truncated...]"


def _truncate_line(line: str, max_length: int) -> str:
    """Truncate `line` to `max_length` characters, appending `_TRUNCATION_SUFFIX` if it was cut."""
    if len(line) <= max_length:
        return line
    return line[:max_length] + _TRUNCATION_SUFFIX


_GREP_RESULT_HEADER_ORDER = (
    "root", "queries", "is_regex", "case_insensitive", "file_glob", "use_gitignore",
    "context_lines", "match_count", "truncated", "cancelled", "gitignored_hidden",
    "results_data_file", "note",
)
"""Key order `GrepTool.format_response()` renders a result dict's header lines in."""


class GrepTool(InterruptibleTool):
    """Recursively searches a directory tree for lines matching any of `queries`.

    Each match is reported with `context.process_config.grep_context_lines` lines of
    surrounding context; overlapping or adjacent context windows within the same file are
    merged. Every matching file appears once in `result["files"]` as `{"filename", "lines"}`.

    A file that can't be decoded as UTF-8 text, or can't be opened at all, is skipped
    silently. Each reported line is truncated to `context.process_config.grep_max_line_length`
    characters. If the JSON serialization of the entire `result["files"]` value would exceed
    `context.process_config.grep_spill_bytes`, it's written to a file in this session's spill
    tmpdir instead and the result carries `results_data_file` in place of `files`.

    Every returned line is redacted before truncation via `self._secret_redactor`. A `query`
    may itself be (or contain) a `[[SECRET:...]]` token echoed back from an earlier result:
    it's resolved to real plaintext before compiling, so matching against a file's real,
    unredacted content still finds the secret it names, but `result["queries"]` always echoes
    back the redacted form.

    See docs/specs/secret-redaction.md.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._max_results = context.process_config.grep_max_results
        self._context_lines = context.process_config.grep_context_lines
        self._max_line_length = context.process_config.grep_max_line_length
        self._spill_bytes = context.process_config.grep_spill_bytes
        self._spill_dir = SpillDir("Grep")
        self._secret_redactor = get_or_create_secret_redactor(context.session)

    def name(self) -> str:
        return "Grep"

    def category(self) -> str:
        return "FILES"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Recursively searches a directory for matches of any of the given "
            "queries, so you can find where text or code appears without reading "
            "each file yourself. Each entry in queries is matched as a literal substring by "
            "default; set is_regex to treat entries as distinct regular expressions instead. "
            "A line matching any query counts as a hit — equivalent to "
            "`grep -e query1 -e query2 ...`. Optionally filter which files are searched with a "
            "filename glob (e.g. '*.py'). path empty means search the whole project. "
            "Results grouped by file under 'files'. "
            "Each line is a string like '*42|matched text' or ' 41|context text'. "
            "Leading '*' marks a matching line. The number is its line number. (Gap "
            "in those numbers marks a break between matches). "
            "Returns at most "
            f"{self._max_results} matches; If 'truncated' is true, then "
            "more matches exist than were returned. A permission-denied subdirectory "
            "is silently skipped. If 'path' is permission-denied, this fails. "
            "Files excluded by gitignore rules are skipped by default; "
            "the result sets 'gitignored_hidden' to true. You can "
            "re-call with use_gitignore=false to search gitignored files too. "
            "Use outputStyle to control output detail: \"ListFiles\" returns just "
            "filenames, \"Matches\" (default) returns only the hit lines, "
            "\"FullContext\" returns hit lines plus surrounding context. "
            "Each reported line is truncated to "
            f"{self._max_line_length} characters (marked with a trailing "
            f"'{_TRUNCATION_SUFFIX}'). If 'files' result would be very large, it's "
            "written to a file instead and the result carries 'results_data_file'. Use ReadFile. "
            "Lines including likely passwords have credentials replaced with token: "
            "`[[SECRET:<type>:<hash>]]`. "
            "Copy the token verbatim into EditFile to match or preserve that line."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "File or directory to search, relative to the project root unless "
                        "absolute. Empty / null means the whole project. "
                        "If filename, only the file is searched; directory search is recursive."
                    ),
                },
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "One or more strings to search for. Lines matching any "
                        "query are returned. Each is a literal substring unless is_regex, "
                        "then each is its own regular expression."
                    ),
                },
                "is_regex": {
                    "type": "boolean",
                    "description": (
                        "Treat queries as list of regular expressions. "
                        "Default false."
                    ),
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Match queries case-insensitively. Default false.",
                },
                "file_glob": {
                    "type": "string",
                    "description": (
                        "Optional filename glob (e.g. '*.py') restricting files searched, "
                        "matched against each file's bare name."
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
                        "Skip files and directories excluded by gitignore rules. Default true. "
                        "Set false to search gitignored files too. "
                        "Ignored if path names a specific filename."
                    ),
                },
            },
            "required": ["queries"],
            "additionalProperties": False,
        }

    def _redact_strings(self, values: list[str]) -> list[str]:
        """Mask likely credentials out of each of `values`."""
        return list(map(lambda value: self._secret_redactor.redact(self.context.session, value), values))

    def apply(self, args: dict[str, Any]) -> Any:
        # None or empty-string default searches recursively from ${workspaceRoot}.
        search_path = args.get("path") or ""
        try:
            queries = validate_queries(coerce_queries_arg(args["queries"]))
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

        # A query may itself be (or contain) a [[SECRET:...]] token echoed back from an earlier
        # ReadFile/Grep result -- resolve it to the real plaintext before compiling, since actual
        # file content on disk holds the secret's real bytes, never the token. `queries` itself
        # (possibly still carrying the token) is what gets echoed back in `result["queries"]`
        # below, never `search_queries` -- see docs/specs/secret-redaction.md.
        search_queries = [
            self._secret_redactor.detokenize(self.context.session, query) for query in queries]
        compiled = compile_queries(
            search_queries, is_regex=is_regex, case_insensitive=case_insensitive)

        # For ListFiles mode, we collect just filenames (deduplicated strings).
        list_files: list[str] = []
        # For Matches/FullContext modes, we collect file dicts with lines.
        files: list[dict[str, Any]] = []
        match_count = 0
        truncated = False
        cancelled = False
        root_path: Path | None = None
        # Set true only when a gitignored file that *would have been searched* (passing file_glob,
        # if given) is skipped — not merely because some gitignored entry exists — so the flag
        # means "some file I'd have searched went unsearched," never "some unrelated ignored file
        # exists." Grep never reads a gitignored file, matching its skip-don't-peek contract.
        gitignored_hidden = False
        # Polled between directories and before reading each file so a Ctrl+C/Escape interrupt stops
        # a search over a large tree promptly, returning whatever matched so far — see
        # `InterruptibleTool`.
        cancel_event = self._active_cancel_event()

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
                            "lines": [_truncate_line(line, self._max_line_length)
                                      for line in self._redact_strings(
                                          matches_only(all_lines, matched_indices))],
                        })
                    else:  # FullContext
                        files.append({
                            "filename": str(single_file),
                            "lines": [_truncate_line(line, self._max_line_length)
                                      for line in self._redact_strings(context_lines_for_matches(
                                          all_lines, matched_indices, self._context_lines))],
                        })
                    match_count += len(matched_indices)
        else:
            # Directory tree search (existing recursive walk).
            for dir_path, _subdirs, filenames, gitignored_filenames, _gitignored_subdirs in (
                    walk_readable_tree(self.context, search_path, use_gitignore=use_gitignore)):
                if root_path is None:
                    root_path = dir_path
                if truncated:
                    break
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                if not gitignored_hidden:
                    for filename in gitignored_filenames:
                        if file_glob and not fnmatch.fnmatch(filename, file_glob):
                            continue
                        gitignored_hidden = True
                        break
                for filename in filenames:
                    if file_glob and not fnmatch.fnmatch(filename, file_glob):
                        continue
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        break
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
                            "lines": [_truncate_line(line, self._max_line_length)
                                      for line in self._redact_strings(
                                          matches_only(all_lines, matched_indices))],
                        })
                    else:  # FullContext
                        files.append({
                            "filename": str(file_path),
                            "lines": [_truncate_line(line, self._max_line_length)
                                      for line in self._redact_strings(context_lines_for_matches(
                                          all_lines, matched_indices, self._context_lines))],
                        })
                    match_count += len(matched_indices)
                    if truncated:
                        break
                if cancelled:
                    break

        logger.debug(
            "Grep found %d match(es) in %d file(s) (truncated=%s, cancelled=%s)",
            match_count, len(files) if output_style != "ListFiles" else len(list_files),
            truncated, cancelled)

        # Redact queries too: a caller may pass a real secret value directly (never obtained via
        # a token at all), and even a query that's already a token must round-trip through here
        # unchanged rather than accidentally being replaced by the searched-for plaintext -- see
        # docs/specs/secret-redaction.md.
        redacted_queries = self._redact_strings(queries)

        if output_style == "ListFiles":
            result: dict[str, Any] = {
                "root": str(root_path) if root_path is not None else search_path,
                "queries": redacted_queries,
                "is_regex": is_regex,
                "case_insensitive": case_insensitive,
                "file_glob": file_glob,
                "use_gitignore": use_gitignore,
                "files": list_files,
                "match_count": match_count,
                "truncated": truncated,
                "cancelled": cancelled,
                "gitignored_hidden": gitignored_hidden,
            }
        else:
            result = {
                "root": str(root_path) if root_path is not None else search_path,
                "queries": redacted_queries,
                "is_regex": is_regex,
                "case_insensitive": case_insensitive,
                "file_glob": file_glob,
                "use_gitignore": use_gitignore,
                "context_lines": self._context_lines if output_style == "FullContext" else 0,
                "files": files,
                "match_count": match_count,
                "truncated": truncated,
                "cancelled": cancelled,
                "gitignored_hidden": gitignored_hidden,
            }
        if gitignored_hidden:
            result["note"] = _GITIGNORE_HIDDEN_NOTE
        self._spill_files_if_needed(result)
        return result

    def _spill_files_if_needed(self, result: dict[str, Any]) -> None:
        """Replace `result["files"]` with a `results_data_file` path if JSON serialization
        exceeds `self._spill_bytes`.

        Raises `ToolCallError` if this `GrepTool` wasn't constructed with a live `Session`.
        """
        encoded = json.dumps(result["files"], ensure_ascii=False).encode("utf-8")
        if len(encoded) <= self._spill_bytes:
            return
        session = self.context.session
        if session is None:
            raise ToolCallError("No session available for spill.", category="business_logic")

        tmp_dir = self._spill_dir.get_or_create(session)
        file_path = tmp_dir / f"grep-results-{secrets.token_hex(4)}.json"
        logger.debug("Grep spilling %d-byte files payload to %s", len(encoded), file_path)
        file_path.write_bytes(encoded)
        os.chmod(file_path, 0o600)
        self._spill_dir.grant_read_access(session, tmp_dir)
        del result["files"]
        result["results_data_file"] = str(file_path)

    def format_response(self, apply_output: Any) -> str:
        """Render `apply_output` as `key: value` header lines in `_GREP_RESULT_HEADER_ORDER`,
        followed by one plain-text block per matched file: the filename on its own line, then
        its already-prefixed `*N|text`/` N|text` lines."""
        header_lines = format_header_lines(
            apply_output, _GREP_RESULT_HEADER_ORDER, known_elsewhere=frozenset({"files"}))
        files = apply_output.get("files")
        if not files:
            return "\n".join(header_lines)
        if isinstance(files[0], str):
            body = "\n".join(files)
        else:
            body = "\n\n".join("\n".join([file["filename"], *file["lines"]]) for file in files)
        return "\n".join(header_lines) + "\n\n" + body

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
        cancelled = " — interrupted" if result.get("cancelled") else ""
        return f"Grep: {queries!r} in {root} ({count}{suffix} match{plural}{cancelled})"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Render the result with `files` capped to its first 20 entries."""
        if error is not None or not isinstance(result, dict) or "files" not in result:
            return super().detail_view(args, result, error)
        files = result["files"]
        if len(files) <= 20:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["files"] = files[:20]
        capped_result["files_omitted"] = len(files) - 20
        return super().detail_view(args, capped_result, error)
