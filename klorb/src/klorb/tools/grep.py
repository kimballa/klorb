# © Copyright 2026 Aaron Kimball
"""A Tool that recursively searches a directory tree for lines matching any of several literal
strings or regular expressions, returning each match with surrounding context lines."""

import fnmatch
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_read
from klorb.search_index.chunk import Chunk
from klorb.tools.exceptions import ToolCallError
from klorb.tools.interruptible_tool import InterruptibleTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.util import (
    SpillDir,
    compile_queries,
    context_lines_for_matches,
    format_match_line,
    get_or_create_secret_redactor,
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

_TRUNCATION_SUFFIX = "[truncated...]"

_VALID_SEARCH_MODES = frozenset({"literal", "regex", "semantic"})
DEFAULT_SEMANTIC_TOP_K = 25
"""Default `top_k`: how many extra semantic chunk hits `search_mode="semantic"` merges in,
independent of `grep_max_results` (which caps literal line hits only)."""


def _truncate_line(line: str, max_length: int) -> str:
    """Truncate `line` to `max_length` characters, appending `_TRUNCATION_SUFFIX` if it was cut —
    guards against a single pathologically long line (a minified sourcemap, a one-line JSON blob)
    dumping an outsized chunk of text into the model's context."""
    if len(line) <= max_length:
        return line
    return line[:max_length] + _TRUNCATION_SUFFIX


class GrepTool(InterruptibleTool):
    """Recursively searches a directory tree for lines matching any of `queries` (each matched
    as a literal substring in `search_mode="literal"` (the default) or `"semantic"`, or as a
    distinct Python regex when `search_mode="regex"` — a line matching any one of them counts as
    a hit, equivalent to `grep -e 'seq1' -e 'seq2' ...`), reusing
    `klorb.tools.util.walk_readable_tree` so the walk obeys `readDirs` at every directory level,
    not just at the given path itself — see that function's docstring for how a denied,
    ask-gated, or symlinked subdirectory is pruned rather than aborting the whole search.

    Each hit is reported with `context.process_config.grep_context_lines` lines of surrounding
    context on each side (like `grep -C`); overlapping or adjacent context windows within the
    same file are merged rather than reported as separately-overlapping results. Every matching
    file appears once in `result["files"]` as `{"filename", "lines"}`, where `lines` is a flat
    list of dense-format strings (see `klorb.tools.util.search_core`) — a leading `*` marks a
    line that itself matched, and each line carries its own 1-based number, so a gap between two
    merged windows shows up as a jump in those numbers with no separator line.

    `search_mode="semantic"` runs the same literal-substring search as `"literal"` and
    additionally merges in up to `top_k` chunk-level hits from the workspace's local hybrid
    (BM25 + vector KNN) search index (see docs/specs/local-search-index.md), scoped by the same
    `path`/`file_glob` the literal search obeys. A file entry a semantic hit contributed to
    carries `match_kind` (`"literal"`, `"semantic"`, or `"literal+semantic"`) and `score` (the
    hit's fused relevance score); a semantic-only hit's dense lines mark every line of its
    matched chunk with `*` (the whole chunk is the unit of relevance, not one exact line).
    Raises `ToolCallError` if the workspace index isn't available (see
    `klorb.session.mixins.core.SessionCoreMixin._create_workspace_indexer`) rather than silently
    degrading to a literal-only result.

    A file that can't be decoded as UTF-8 text, or can't be opened at all, is skipped silently
    (treated as binary) rather than raising — matches are only ever reported from files
    readable as text, matching common `grep -I` behavior.

    Each reported line is truncated to `context.process_config.grep_max_line_length` characters
    (with a `"[truncated...]"` suffix) to guard against a single pathologically long line — a
    minified sourcemap, a one-line JSON blob — dumping an outsized chunk of text into the
    model's context. If the JSON serialization of the entire `result["files"]` value would
    exceed `context.process_config.grep_spill_bytes`, it's written to a file in this session's
    spill tmpdir instead and the result carries `results_data_file` in place of `files` — the
    same `klorb.tools.util.spill.SpillDir` mechanism `WebFetchTool` uses for its own response
    bodies, so a session that calls Grep repeatedly reuses one tmpdir rather than accumulating
    one per call. See `_spill_files_if_needed`.

    Every returned line is redacted before truncation (never after — truncating first could slice
    a credential in half, leaving an undetectable fragment in the output) via
    `self._secret_redactor` (a `klorb.tools.util.SecretRedactor`, the same type `ReadFileTool`
    uses) — see docs/specs/secret-redaction.md. A `query` may itself be (or contain) a
    `[[SECRET:...]]` token echoed back from an earlier `ReadFile`/`Grep` result: it's resolved to
    real plaintext before compiling, so matching against a file's real, unredacted content still
    finds the secret it names, but `result["queries"]` always echoes back the redacted form —
    never the plaintext, whether that plaintext came from detokenizing a query or was typed
    directly.
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
            "Recursively searches a directory tree for lines matching any of the given "
            "queries, so you can find where a piece of text or code appears without reading "
            "every file yourself. Each entry in queries is matched as a literal substring "
            "under search_mode \"literal\" (the default) or \"semantic\"; set search_mode to "
            "\"regex\" to treat every entry as a distinct Python regular expression instead. "
            "\"semantic\" additionally merges in up to top_k related chunks from the "
            "workspace's local search index, found by meaning rather than exact wording — "
            "useful when you don't know the exact string to search for. A line matching any "
            "one query counts as a hit — equivalent to "
            "`grep -e query1 -e query2 ...`. Optionally filter which files are searched with a "
            "filename glob (e.g. '*.py'). path empty means search the whole project root. "
            "Results are grouped by file under 'files'; "
            "each line is a string like '*42|matched text' or ' 41|context text', where the "
            "leading '*' marks a matching line (or, for a semantic-only hit, every line of the "
            "matched chunk) and the number is its 1-based line number (a gap in those numbers "
            "marks a break between context windows). A file a semantic hit contributed to "
            "carries match_kind and score. "
            "Line numbers can be used directly as input to EditFile start_line / end_line. "
            "Returns at most "
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
            "\"FullContext\" returns hit lines plus surrounding context. "
            "Each reported line is truncated to "
            f"{self._max_line_length} characters (marked with a trailing "
            f"'{_TRUNCATION_SUFFIX}'). If the 'files' result would be very large, it's "
            "written to a file instead and the result carries 'results_data_file' (a path "
            "you can ReadFile) in place of 'files'. "
            "A line that looks like it holds a credential may come back with the secret "
            "replaced by a `[[SECRET:<type>:<hash>]]` token, same convention as ReadFile -- "
            "copy the token verbatim into EditFile to match or preserve that line."
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
                "search_mode": {
                    "type": "string",
                    "enum": ["literal", "regex", "semantic"],
                    "description": (
                        "How to interpret each entry in queries. \"literal\" (default): a "
                        "plain substring match. \"regex\": each entry is its own Python "
                        "regular expression. \"semantic\": runs the same literal substring "
                        "search as \"literal\", plus a hybrid lexical+vector search over the "
                        "workspace's local search index, merging in up to top_k additional "
                        "chunk-level hits ranked by relevance -- useful for finding code "
                        "related to a concept even when the exact wording differs."
                    ),
                },
                "top_k": {
                    "type": "integer",
                    "description": (
                        "Maximum extra semantic chunk hits to merge in when search_mode is "
                        f"\"semantic\". Defaults to {DEFAULT_SEMANTIC_TOP_K}. Ignored for "
                        "other search_mode values."
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

    def _redact_strings(self, values: list[str]) -> list[str]:
        """Mask likely credentials out of each of `values` before it's returned -- see
        docs/specs/secret-redaction.md. Used both for dense-format result lines and for the
        echoed `queries` list, so it takes plain strings rather than anything line-shaped."""
        return list(map(lambda value: self._secret_redactor.redact(self.context.session, value), values))

    def apply(self, args: dict[str, Any]) -> Any:
        # None or empty-string default searches recursively from ${workspaceRoot}.
        search_path = args.get("path") or ""
        try:
            queries = validate_queries(args["queries"])
        except KeyError:
            raise ValueError(
                "Missing required argument: 'queries'. Provide a non-empty array of search strings.")
        search_mode = args.get("search_mode", "literal")
        if search_mode not in _VALID_SEARCH_MODES:
            raise ValueError(
                f"Invalid search_mode {search_mode!r}. Valid values are: "
                f"{', '.join(sorted(_VALID_SEARCH_MODES))}.")
        is_regex = search_mode == "regex"
        top_k = args.get("top_k", DEFAULT_SEMANTIC_TOP_K)
        case_insensitive = args.get("case_insensitive", False)
        file_glob = args.get("file_glob")
        use_gitignore = args.get("use_gitignore", True)
        output_style = validate_output_style(args.get("outputStyle"))
        logger.debug(
            "Grep %r in %r (search_mode=%s, case_insensitive=%s, file_glob=%s, use_gitignore=%s, "
            "outputStyle=%s)",
            queries, search_path, search_mode, case_insensitive, file_glob, use_gitignore,
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

        if search_mode == "semantic" and not cancelled:
            workspace_root = self.context.session_config.workspace.path.resolve()
            scope_path = single_file if single_file is not None else (root_path or workspace_root)
            match_count += self._merge_semantic_matches(
                search_queries, workspace_root, scope_path,
                scope_is_file=single_file is not None, file_glob=file_glob, top_k=top_k,
                output_style=output_style, files=files, list_files=list_files)

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
                "search_mode": search_mode,
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
                "search_mode": search_mode,
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

    def _merge_semantic_matches(
        self, search_queries: list[str], workspace_root: Path, scope_path: Path, *,
        scope_is_file: bool, file_glob: str | None, top_k: int,
        output_style: str, files: list[dict[str, Any]], list_files: list[str],
    ) -> int:
        """Run a hybrid (BM25 + vector KNN) search for each of `search_queries` against the
        workspace's local index, scoped to `scope_path` (an exact file if `scope_is_file`, else a
        directory tree) and `file_glob`, then merge the top `top_k` fused hits into `files`/
        `list_files` in place. Returns the number of matched lines the merge added to
        `match_count`. Raises `ToolCallError` if the workspace index isn't available.
        """
        indexer = self.context.session.workspace_indexer if self.context.session is not None else None
        if indexer is None:
            raise ToolCallError(
                "The workspace search index isn't available for search_mode=\"semantic\" "
                "(the workspace may be untrusted, the feature may be disabled, or `klorb init` "
                "may not have run). Retry with search_mode=\"literal\" or \"regex\" instead.",
                category="business_logic")

        best_by_chunk_id: dict[str, tuple[Chunk, float]] = {}
        for query in search_queries:
            for chunk, score in indexer.hybrid_search(query, limit=top_k * 4):
                existing = best_by_chunk_id.get(chunk.chunk_id)
                if existing is None or score > existing[1]:
                    best_by_chunk_id[chunk.chunk_id] = (chunk, score)

        in_scope = [
            (chunk, score) for chunk, score in best_by_chunk_id.values()
            if self._chunk_in_scope(chunk, workspace_root, scope_path, scope_is_file, file_glob)
        ]
        in_scope.sort(key=lambda pair: pair[1], reverse=True)

        added_lines = 0
        files_by_name = {entry["filename"]: entry for entry in files}
        for chunk, score in in_scope[:top_k]:
            abs_path = workspace_root / chunk.source_path
            dense_lines = self._render_chunk_lines(abs_path, chunk)
            if dense_lines is None:
                continue
            filename = str(abs_path)
            added_lines += len(dense_lines)
            if output_style == "ListFiles":
                if filename not in list_files:
                    list_files.append(filename)
                continue
            existing_entry = files_by_name.get(filename)
            if existing_entry is None:
                new_entry = {
                    "filename": filename, "lines": dense_lines,
                    "match_kind": "semantic", "score": score,
                }
                files.append(new_entry)
                files_by_name[filename] = new_entry
            else:
                existing_entry["lines"].extend(dense_lines)
                existing_entry["match_kind"] = "literal+semantic"
                existing_entry["score"] = max(existing_entry.get("score", 0.0), score)
        return added_lines

    @staticmethod
    def _chunk_in_scope(
        chunk: Chunk, workspace_root: Path, scope_path: Path, scope_is_file: bool,
        file_glob: str | None,
    ) -> bool:
        abs_path = (workspace_root / chunk.source_path).resolve()
        if scope_is_file:
            if abs_path != scope_path:
                return False
        else:
            try:
                abs_path.relative_to(scope_path)
            except ValueError:
                return False
        return not file_glob or fnmatch.fnmatch(abs_path.name, file_glob)

    def _render_chunk_lines(self, abs_path: Path, chunk: Chunk) -> list[str] | None:
        """`chunk`'s line span rendered in dense format, every line marked `*` (the whole chunk
        is the unit of semantic relevance, not one exact matching line) -- re-reading `abs_path`
        fresh rather than reusing `chunk.text`, since a structural chunk's `text` is a synthesized
        synopsis for some kinds (see `klorb.search_index.chunkers._tree_sitter_base`), not
        necessarily the file's real content over that line range. `None` if `abs_path` can no
        longer be read (deleted or changed since it was indexed)."""
        try:
            text = abs_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        all_lines = text.splitlines()
        start = max(0, chunk.start_line - 1)
        end = min(len(all_lines), chunk.end_line)
        dense_lines = [format_match_line(i + 1, all_lines[i], matched=True) for i in range(start, end)]
        return [_truncate_line(line, self._max_line_length)
                for line in self._redact_strings(dense_lines)]

    def _spill_files_if_needed(self, result: dict[str, Any]) -> None:
        """Replace `result["files"]` with a `results_data_file` path if its JSON serialization
        exceeds `self._spill_bytes`, so a search matching many files/lines can't dump an
        outsized payload straight into the model's context. Exactly one of `files`/
        `results_data_file` is present in the returned result. Writes into this session's
        shared `Grep` spill tmpdir (`self._spill_dir`), reused across every call in the same
        session rather than a fresh directory per spill — see `klorb.tools.util.spill.SpillDir`.
        Raises `ToolCallError` if this `GrepTool` wasn't constructed with a live `Session`
        (`self.context.session`), since the tmpdir is tracked in `Session.tool_state`.
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
