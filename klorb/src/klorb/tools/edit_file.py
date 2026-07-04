# © Copyright 2026 Aaron Kimball
"""A Tool that replaces a verified, inclusive line range in a text file for a model."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import evaluate_write, resolve_within_workspace
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool
from klorb.tools.tool import truncate_lines

logger = logging.getLogger(__name__)


class EditFileTool(Tool):
    """Replaces the inclusive line range `[start_line, end_line]` of a text file with
    `new_text`, after locating `start_text`/`end_text` at (or near) those lines.
    `start_line`/`end_line` are treated as a location hint rather than a hard requirement: if
    the file has drifted since the caller last saw it (e.g. from an earlier edit in the same
    turn shifting later lines), `apply()` searches within `context.process_config
    .edit_file_drift_search_radius` lines of the hint for a unique location where
    `start_text`/`end_text` still match at the same relative span, and edits there instead of
    failing — see `_find_drift_candidates`. No match within that radius, or more than one,
    still raises `ValueError`.

    There is no separate insert or delete tool: insert without deleting by setting
    `start_line == end_line` and folding that line's original text into `new_text`; delete by
    passing an empty `new_text`. The one exception is an empty file (`total_lines == 0`),
    where there's no anchor line to replace — the only valid call there is
    `start_line=1, end_line=0, start_text="", end_text=""`.

    `filename` is confined to `SessionConfig.workspace_root` and further checked against
    `writeDirs` (see `klorb.permissions.workspace.evaluate_write`) before any disk I/O.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._drift_search_radius = context.process_config.edit_file_drift_search_radius

    def name(self) -> str:
        return "EditFile"

    def description(self) -> str:
        return (
            "Replaces the inclusive 1-indexed line range [start_line, end_line] of a text "
            "file with new_text. start_text, end_text, new_text, context_before, and "
            "context_after are all raw file content: never include the 'N|' line-number "
            "prefix that ReadFile prepends to each line for display purposes. start_text "
            "and end_text are each one line of text: the contents of the first and last "
            "lines to replace, and must match the first and last lines exactly, without "
            "any trailing newline ('\\n'). The associated line numbers are given in "
            "start_line and end_line. start_line/end_line are only a location hint, not "
            "exact coordinates: some offset drift (e.g. from an earlier edit) is "
            "tolerated — if start_text/end_text don't match exactly at the hint but "
            "match together, at the same relative offset, within a few lines of it, the "
            "edit is applied there. The response will report the corrected "
            "start_line/end_line plus line_hint_matched=false. re-ReadFile is only "
            "needed when no matching location is found nearby. When more than one "
            "match is found ('Ambiguous match' error), retry with context_before / "
            "context_after arguments (lines of raw content immediately before "
            "start_line / after end_line, unique to the intended location) to "
            "disambiguate, exactly as reported in the error. To insert new content "
            "into a non-empty file without deleting anything set start_line == "
            "end_line to an existing line number and fold that line's original text "
            "into new_text alongside the new content. Setting end_line to start_line "
            "minus 1 (a zero-width range) is invalid for any non-empty file. To "
            "insert into a completely empty file (0 lines), the only valid call is "
            "start_line=1, end_line=0, start_text=\"\", end_text=\"\". To delete "
            "lines, pass empty new_text. Applying multiple edits to the same file "
            "bottom-to-top (greatest line numbers first) avoids drift."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Path to the text file to edit.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "1-indexed line to start replacing from.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "1-indexed, inclusive line to stop replacing at.",
                },
                "start_text": {
                    "type": "string",
                    "description": (
                        "Expected current single-line content of start_line, for drift verification."
                    ),
                },
                "end_text": {
                    "type": "string",
                    "description": (
                        "Expected current content of end_line."
                    ),
                },
                "new_text": {
                    "type": "string",
                    "description": (
                        "Replacement content for the range. Empty string deletes the range."
                    ),
                },
                "context_before": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional. Raw content (no line numbers) immediately before start_line, "
                        "used only to disambiguate multiple start_text/end_text matches. "
                        "Only include after an 'Ambiguous match' error. The empty string \"\" "
                        "means that there must be absolutely nothing before start_line "
                        "(i.e. it's the file's first line). To omit, just send null."
                    ),
                },
                "context_after": {
                    "type": ["string", "null"],
                    "description": (
                        "Optional. Like context_before, but matches immediately after end_line "
                        "-- and \"\" asserts end_line is the file's last line."
                    ),
                },
            },
            "required": ["filename", "start_line", "end_line", "start_text", "end_text", "new_text"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        filename = args["filename"]
        start_line = args["start_line"]
        end_line = args["end_line"]
        start_text = args["start_text"]
        end_text = args["end_text"]
        new_text = args["new_text"]
        context_before = args.get("context_before")
        context_after = args.get("context_after")
        logger.debug("EditFile %s (start_line=%s, end_line=%s)", filename, start_line, end_line)

        for label, value in (("start_line", start_line), ("end_line", end_line)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{label} must be an integer, got {value!r} ({type(value).__name__})")

        for label, value in (("start_text", start_text), ("end_text", end_text)):
            if "\n" in value:
                first_line = value.split("\n", 1)[0]
                raise ValueError(
                    f"{label} must be exactly one line, with no '\\n' character: got "
                    f"{value!r}, which spans multiple lines. Send only the single line of "
                    f"{'start_line' if label == 'start_text' else 'end_line'}'s raw content, "
                    f"not the whole range being replaced. Did you mean {first_line!r}?")

        path = resolve_within_workspace(self.context, filename)
        raise_if_not_allowed(
            evaluate_write(self.context, path), resource_description=f"write to {path}",
            path=path, is_write=True)

        raw = path.read_text(encoding="utf-8")
        original_ends_with_newline = raw.endswith("\n")
        all_lines = raw.splitlines()
        total_lines = len(all_lines)

        if total_lines == 0:
            if not (start_line == 1 and end_line == 0):
                raise ValueError(
                    f"{filename} is empty; the only valid edit is "
                    "start_line=1, end_line=0, start_text=\"\", end_text=\"\" to insert content")
            if start_text != "" or end_text != "":
                raise ValueError(
                    f"{filename} is empty; start_text and end_text must both be \"\", "
                    f"got start_text={start_text!r}, end_text={end_text!r}")
            resolved_start_line, resolved_end_line, line_hint_matched = start_line, end_line, True
        else:
            if start_line < 1 or end_line < start_line or end_line > total_lines:
                raise ValueError(
                    f"start_line={start_line}, end_line={end_line} out of range for a "
                    f"{total_lines}-line file (expected 1 <= start_line <= end_line <= {total_lines})")

            candidates = self._find_drift_candidates(
                all_lines, start_line=start_line, end_line=end_line, start_text=start_text,
                end_text=end_text, context_before=context_before, context_after=context_after)

            if len(candidates) == 1:
                resolved_start_line = candidates[0]
                resolved_end_line = resolved_start_line + (end_line - start_line)
                line_hint_matched = resolved_start_line == start_line
            elif len(candidates) == 0:
                if all_lines[start_line - 1] != start_text:
                    raise ValueError(
                        f"start_text does not match line {start_line}'s actual content: "
                        f"{all_lines[start_line - 1]!r} != {start_text!r}, and no matching "
                        f"location was found within {self._drift_search_radius} lines; "
                        f"re-ReadFile {filename}")
                if all_lines[end_line - 1] != end_text:
                    raise ValueError(
                        f"end_text does not match line {end_line}'s actual content: "
                        f"{all_lines[end_line - 1]!r} != {end_text!r}, and no matching "
                        f"location was found within {self._drift_search_radius} lines; "
                        f"re-ReadFile {filename}")
                raise ValueError(
                    f"start_text/end_text match line {start_line}..{end_line}, but the "
                    "supplied context_before/context_after does not match the surrounding "
                    f"lines there (and no other nearby location within "
                    f"{self._drift_search_radius} lines satisfies every constraint); omit or "
                    f"correct context_before/context_after, or re-ReadFile {filename} to "
                    "confirm the surrounding lines")
            else:
                span = end_line - start_line
                preview_lines = self._minimal_disambiguating_window(all_lines, candidates, span)
                descriptions = "; ".join(
                    self._describe_candidate_neighbors(all_lines, candidate, span, preview_lines)
                    for candidate in candidates)
                raise ValueError(
                    f"Ambiguous match: start_text/end_text for start_line={start_line}, "
                    f"end_line={end_line} matches at {len(candidates)} locations within "
                    f"{self._drift_search_radius} lines (lines {candidates}): {descriptions}. "
                    "Retry with a start_line matching the intended location, or with the "
                    "context_before/context_after values shown above for that location, to "
                    "disambiguate — note that \"\" there means \"assert nothing is on that "
                    "side\" (e.g. genuinely the file's first/last line), which is different "
                    "from omitting the argument entirely (\"don't check that side\")")

        new_lines = (
            all_lines[:resolved_start_line - 1] + new_text.splitlines() + all_lines[resolved_end_line:])

        if resolved_end_line == total_lines:
            terminate_with_newline = bool(new_lines)
        else:
            terminate_with_newline = original_ends_with_newline

        content = "\n".join(new_lines)
        if terminate_with_newline:
            content += "\n"
        path.write_text(content, encoding="utf-8")

        new_total_lines = len(new_lines)
        inserted_line_count = len(new_text.splitlines())
        snippet_end = resolved_start_line + inserted_line_count - 1
        snippet = "\n".join(
            f"{resolved_start_line + i}|{line}" for i, line in enumerate(new_text.splitlines()))

        logger.debug(
            "EditFile %s replaced lines %d-%d (now %d-%d) of what is now a %d-line file",
            filename, resolved_start_line, resolved_end_line, resolved_start_line, snippet_end,
            new_total_lines,
        )

        return {
            "filename": filename,
            "requested_start_line": start_line,
            "requested_end_line": end_line,
            "start_line": resolved_start_line,
            "end_line": snippet_end,
            "line_hint_matched": line_hint_matched,
            "new_total_lines": new_total_lines,
            "content": snippet,
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """`"Edit file: foo.py (+A/-R)"`, where the added/removed line counts are computed from
        the call's own `start_line`/`end_line`/`new_text` args (not `result`), so the summary
        is identical on success and failure and unaffected by drift relocation, which preserves
        the requested span's length.
        """
        filename = args.get("filename", "?")
        diff = ""
        start_line, end_line, new_text = args.get("start_line"), args.get("end_line"), args.get("new_text")
        if isinstance(start_line, int) and isinstance(end_line, int) and isinstance(new_text, str):
            removed = end_line - start_line + 1
            added = new_text.count("\n") + 1 if new_text else 0
            diff = f" (+{added}/-{removed})"
        base = f"Edit file: {filename}{diff}"
        return base if error is None else f"{base} failed: {error}"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["content"]` (the edited
        region) capped to 8 lines — a full-file rewrite via one large `new_text` could otherwise
        dump hundreds of lines here.
        """
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["content"] = truncate_lines(result["content"], 8)
        return super().detail_view(args, capped_result, error)

    def _describe_candidate_neighbors(
        self, all_lines: list[str], candidate_start: int, span: int, preview_lines: int,
    ) -> str:
        """One-line description of a drift-search candidate's actual nearby lines (up to
        `preview_lines` on each side, fewer near a file's start/end), for the "Ambiguous
        match" error: naming the exact `context_before`/`context_after` *value* that singles
        out this candidate lets a model copy it verbatim rather than reconstruct it from a
        separate `ReadFile` call and risk miscounting which lines are truly adjacent (the same
        off-by-one mistake drift-tolerant relocation itself is meant to route around, just
        relocated into `context_before`/`context_after` instead of `start_line`/`end_line`). At
        a file's start/end, where there's nothing to show, the description says so explicitly
        and recommends the empty string `""`, which — unlike omitting the argument — asserts
        there's genuinely nothing on that side (see `_context_matches_candidate`).
        `preview_lines` comes from `_minimal_disambiguating_window`, not a fixed constant,
        since one line is not always enough (see that method's docstring)."""
        candidate_end = candidate_start + span
        before_lines = all_lines[max(0, candidate_start - 1 - preview_lines):candidate_start - 1]
        after_lines = all_lines[candidate_end:candidate_end + preview_lines]
        before_value = "\n".join(before_lines)
        after_value = "\n".join(after_lines)
        before = (
            f"context_before={before_value!r}" if before_lines
            else "context_before='' (nothing precedes -- start of file)")
        after = (
            f"context_after={after_value!r}" if after_lines
            else "context_after='' (nothing follows -- end of file)")
        return f"line {candidate_start} ({before}, {after})"

    def _minimal_disambiguating_window(
        self, all_lines: list[str], candidates: list[int], span: int,
    ) -> int:
        """Smallest number of context lines (on each side) such that every candidate in
        `candidates` is uniquely singled out by copying its own natural surrounding content as
        `context_before`/`context_after` (see `_candidate_resolved_at`) — expanding from 1
        upward. A single line is not always enough: two candidates can share the same
        immediate neighbor (e.g. the first two of three identical lines both have another
        identical line right after them) while still being distinguishable a line or two
        further out.

        This always terminates with every candidate resolved, at the latest once expansion
        reaches a candidate's own true maximal before/after line counts (all the way to the
        file's start and end): at that point no other candidate can share the same pair of
        counts (a different line can't be the same distance from both the file's start and
        its end), so `_context_matches_candidate`'s bounds checks alone rule out every other
        candidate there, independent of content.
        """
        n = 1
        while not all(
            self._candidate_resolved_at(all_lines, candidate, candidates, span, n)
            for candidate in candidates
        ):
            n += 1
        return n

    def _candidate_resolved_at(
        self, all_lines: list[str], candidate: int, candidates: list[int], span: int, n: int,
    ) -> bool:
        """True if copying `candidate`'s own up-to-`n`-line before/after content as
        `context_before`/`context_after` (naturally shorter near a file boundary, becoming the
        empty string `""` -- asserting nothing is there, per `_context_matches_candidate` --
        when `candidate` is genuinely at the file's start/end) would single it out uniquely
        among `candidates`, per the same `_context_matches_candidate` check a real call goes
        through."""
        candidate_end = candidate + span
        context_before = "\n".join(all_lines[max(0, candidate - 1 - n):candidate - 1])
        context_after = "\n".join(all_lines[candidate_end:candidate_end + n])
        matches = [
            other for other in candidates
            if self._context_matches_candidate(
                all_lines, candidate_start=other, candidate_end=other + span,
                context_before=context_before, context_after=context_after)
        ]
        return matches == [candidate]

    def _find_drift_candidates(
        self, all_lines: list[str], *, start_line: int, end_line: int, start_text: str,
        end_text: str, context_before: str | None, context_after: str | None,
    ) -> list[int]:
        """Return every start-line position within `self._drift_search_radius` of `start_line`
        (inclusive, clipped to the file's bounds) where `start_text` occurs and `end_text`
        occurs `end_line - start_line` lines later — preserving the caller's requested span
        length — and, if `context_before`/`context_after` are given, where those also match
        (see `_context_matches_candidate`). `start_line` itself (zero drift) is an ordinary
        candidate, not special-cased, so an undrifted hint naturally resolves as the sole
        match. Candidates are returned in ascending line-number order.
        """
        total_lines = len(all_lines)
        span = end_line - start_line
        radius = self._drift_search_radius
        lo = max(1, start_line - radius)
        hi = min(total_lines - span, start_line + radius)

        candidates: list[int] = []
        for candidate_start in range(lo, hi + 1):
            candidate_end = candidate_start + span
            if all_lines[candidate_start - 1] != start_text:
                continue
            if all_lines[candidate_end - 1] != end_text:
                continue
            if not self._context_matches_candidate(
                all_lines, candidate_start=candidate_start, candidate_end=candidate_end,
                context_before=context_before, context_after=context_after,
            ):
                continue
            candidates.append(candidate_start)
        return candidates

    def _context_matches_candidate(
        self, all_lines: list[str], *, candidate_start: int, candidate_end: int,
        context_before: str | None, context_after: str | None,
    ) -> bool:
        """True if `context_before`'s lines (top-to-bottom, ending immediately before
        `candidate_start`) and `context_after`'s lines (top-to-bottom, starting immediately
        after `candidate_end`) match the file's actual content there. `None` (the argument
        omitted entirely) skips that side's check; `""` (explicitly given as empty) instead
        asserts there must be *nothing* on that side — `candidate_start`/`candidate_end` must
        be the file's actual first/last line — since an omitted argument and an explicit
        empty string are otherwise indistinguishable but mean opposite things ("don't care"
        vs. "must be the start/end of the file"). A candidate whose required non-empty
        context window would run off the file's start/end fails the match — a file too short
        near one edge is an ordinary reason a candidate is wrong, not a malformed request.
        """
        if context_before is not None:
            if context_before == "":
                if candidate_start != 1:
                    return False
            else:
                before_lines = context_before.splitlines()
                if candidate_start - 1 - len(before_lines) < 0:
                    return False
                if all_lines[candidate_start - 1 - len(before_lines):candidate_start - 1] != before_lines:
                    return False
        if context_after is not None:
            if context_after == "":
                if candidate_end != len(all_lines):
                    return False
            else:
                after_lines = context_after.splitlines()
                if candidate_end + len(after_lines) > len(all_lines):
                    return False
                if all_lines[candidate_end:candidate_end + len(after_lines)] != after_lines:
                    return False
        return True
