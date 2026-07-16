# © Copyright 2026 Aaron Kimball
"""The drift-tolerant row-extent substitution mechanic shared by `EditFileTool` and
`EditScratchpadTool` — see `klorb.tools.util`'s package docstring for how the two tools each
hold one of these as a member and delegate to it."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LineRangeEdit:
    """Resolved outcome of one row-extent substitution against a subject's current lines (see
    `EditFileCore._resolve_line_range_edit`): where it actually landed (`resolved_start_line`/
    `resolved_end_line`), whether that matched the caller's hint exactly with no drift
    (`line_hint_matched`), and the subject's full line list after the substitution (`new_lines`).

    `fuzzy_whitespace_match` is True when the anchors could only be located by ignoring
    leading/trailing whitespace on `start_text`/`end_text` (and the file's lines) after an exact
    match failed; `whitespace_message` then carries the advisory string `apply()` echoes back to
    the caller (None when no fuzzy fallback occurred).
    """

    resolved_start_line: int
    resolved_end_line: int
    line_hint_matched: bool
    new_lines: list[str]
    fuzzy_whitespace_match: bool = False
    whitespace_message: str | None = None


class EditFileCore:
    """Replaces the inclusive line range `[start_line, end_line]` of `path` with `new_text`,
    after locating `start_text`/`end_text` at (or near) those lines, tolerating up to
    `drift_search_radius` lines of drift — the shared mechanic behind `EditFileTool` and
    `EditScratchpadTool`. See `klorb.tools.edit_file.EditFileTool` for the full user-facing
    contract this implements.
    """

    def __init__(self, drift_search_radius: int) -> None:
        self._drift_search_radius = drift_search_radius

    def parameter_properties(self) -> dict[str, Any]:
        """Return the `start_line`/`end_line`/`start_text`/`end_text`/`new_text`/
        `context_before`/`context_after` JSON-schema properties shared by `EditFileTool` and
        `EditScratchpadTool`'s `parameters()` — each adds its own `filename` property (or not)
        and `required` list around this."""
        return {
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
                    "The CURRENT content at start_line — one line, verbatim, no "
                    "trailing newline. For verifying you're editing the right spot."
                ),
            },
            "end_text": {
                "type": "string",
                "description": (
                    "The CURRENT content at end_line — one line, verbatim, no "
                    "trailing newline. Same rule as start_text."
                ),
            },
            "new_text": {
                "type": "string",
                "description": (
                    "Full replacement content for [start_line, end_line], as raw text with "
                    "'\\n' between lines. Unlike start_text/end_text, this may span any "
                    "number of lines, including zero (an empty string deletes the range)."
                ),
            },
            "context_before": {
                "type": ["string", "null"],
                "description": (
                    "Optional. Raw content (no line numbers) immediately before start_line, "
                    "used to disambiguate multiple start_text/end_text matches. Only "
                    "include after an 'Ambiguous match' error. The empty string \"\" means "
                    "absolutely nothing before start_line (i.e. it's the very "
                    "first line). To omit, just send null."
                ),
            },
            "context_after": {
                "type": ["string", "null"],
                "description": (
                    "Optional. Like context_before, but matches immediately after end_line -- "
                    "and \"\" asserts end_line is the very last line."
                ),
            },
        }

    def apply(self, path: Path, args: dict[str, Any], *, subject: str, reread_hint: str) -> dict[str, Any]:
        """Apply one row-extent substitution to `path` per `args`, returning
        `requested_start_line`, `requested_end_line`, `start_line`, `end_line`,
        `line_hint_matched`, `new_total_lines`, and `content` (the caller adds `filename` if it
        has one).

        `subject` names the thing being edited for the empty-subject error message (e.g. a
        filename, or `"the scratchpad"`). `reread_hint` is substituted into every raised
        `ValueError`'s suggestion for what to do next (e.g. `"re-ReadFile foo.py"` or
        `"re-ReadScratchpad your scratchpad"`), so the message stays accurate for whichever
        caller (`EditFileTool`/`EditScratchpadTool`) invoked this.
        """
        try:
            start_line = args["start_line"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'start_line'. Provide the 1-based line number where "
                "the edit begins.")
        try:
            end_line = args["end_line"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'end_line'. Provide the 1-based line number where the "
                "edit ends.")
        try:
            start_text = args["start_text"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'start_text'. Provide the exact current content of "
                "start_line, to confirm the edit is anchored correctly.")
        try:
            end_text = args["end_text"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'end_text'. Provide the exact current content of "
                "end_line, to confirm the edit is anchored correctly.")
        try:
            new_text = args["new_text"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'new_text'. Provide the replacement text for the "
                "start_line..end_line range.")
        context_before = args.get("context_before")
        context_after = args.get("context_after")

        for label, value in (("start_line", start_line), ("end_line", end_line)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{label} must be an integer, got {value!r} ({type(value).__name__})")

        truncated_start = False
        truncated_end = False
        if "\n" in start_text:
            start_text = start_text.split("\n", 1)[0]
            truncated_start = True
        if "\n" in end_text:
            end_text = end_text.split("\n", 1)[0]
            truncated_end = True

        # Validate types after truncation so callers who accidentally pasted a multi-line
        # block into start_text/end_text get the graceful behavior of using just the first
        # line as the anchor, rather than a hard error.
        for label, value in (("start_text", start_text), ("end_text", end_text)):
            if "\n" in value:
                first_line = value.split("\n", 1)[0]
                raise ValueError(
                    f"{label} must be exactly one line, with no '\\n' character: got "
                    f"{value!r}, which spans multiple lines. Send only the single line of "
                    f"{'start_line' if label == 'start_text' else 'end_line'}'s raw content, "
                    f"not the whole range being replaced. Did you mean {first_line!r}?")

        raw = path.read_text(encoding="utf-8")
        original_ends_with_newline = raw.endswith("\n")
        all_lines = raw.splitlines()
        total_lines = len(all_lines)

        edit = self._resolve_line_range_edit(
            all_lines, start_line=start_line, end_line=end_line, start_text=start_text,
            end_text=end_text, new_text=new_text, context_before=context_before,
            context_after=context_after, subject=subject, reread_hint=reread_hint)
        resolved_start_line = edit.resolved_start_line
        resolved_end_line = edit.resolved_end_line
        new_lines = edit.new_lines

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

        out: dict[str, Any] = {
            "requested_start_line": start_line,
            "requested_end_line": end_line,
            "start_line": resolved_start_line,
            "end_line": snippet_end,
            "line_hint_matched": edit.line_hint_matched,
            "new_total_lines": new_total_lines,
            "content": snippet,
        }

        # When the anchors could only be located by ignoring leading/trailing whitespace,
        # surface that so the caller knows to be more precise next time.
        if edit.fuzzy_whitespace_match:
            out["fuzzyWhitespaceMatch"] = True
            out["whitespace"] = edit.whitespace_message

        # If we truncated start_text/end_text, provide advisory feedback to steer the agent to
        # more-efficient usage for next time.
        user_feedback = []
        if truncated_start:
            user_feedback.append("btw, start_text should only be one line - the first line of the block to "
                                 "replace. The first '\n' and everything afterward was ignored. Next time, "
                                 "save tokens! Provide only one line of start_text to begin the replacement "
                                 "region match.")
        if truncated_end:
            user_feedback.append("btw, end_text should only be one line - the last line of the block to "
                                 "replace. The first '\n' and everything afterward was ignored. Next time, "
                                 "save tokens! Provide only one line of end_text to match the end of the "
                                 "replacement region.")
        if len(user_feedback):
            out['feedback'] = user_feedback

        return out

    def _resolve_line_range_edit(
        self, all_lines: list[str], *, start_line: int, end_line: int, start_text: str,
        end_text: str, new_text: str, context_before: str | None, context_after: str | None,
        subject: str, reread_hint: str,
    ) -> LineRangeEdit:
        """Locate the inclusive line range `[start_line, end_line]` within `all_lines` and
        return `all_lines` with that range replaced by `new_text`'s lines.

        Raises `ValueError` for an out-of-range span, a `start_text`/`end_text` mismatch with no
        nearby drift candidate, or an ambiguous match among more than one drift candidate.
        """
        total_lines = len(all_lines)

        if total_lines == 0:
            if not (start_line == 1 and end_line == 0):
                raise ValueError(
                    f"{subject} is empty; the only valid edit is "
                    "start_line=1, end_line=0, start_text=\"\", end_text=\"\" to insert content")
            if start_text != "" or end_text != "":
                raise ValueError(
                    f"{subject} is empty; start_text and end_text must both be \"\", "
                    f"got start_text={start_text!r}, end_text={end_text!r}")
            return LineRangeEdit(
                resolved_start_line=start_line, resolved_end_line=end_line, line_hint_matched=True,
                new_lines=new_text.splitlines())

        if start_line < 1 or end_line < start_line or end_line > total_lines:
            raise ValueError(
                f"start_line={start_line}, end_line={end_line} out of range for a "
                f"{total_lines}-line subject (expected 1 <= start_line <= end_line <= {total_lines})")

        candidates = self._find_drift_candidates(
            all_lines, start_line=start_line, end_line=end_line, start_text=start_text,
            end_text=end_text, context_before=context_before, context_after=context_after)

        fuzzy = False
        whitespace_message: str | None = None
        if len(candidates) == 1:
            resolved_start_line = candidates[0]
            resolved_end_line = resolved_start_line + (end_line - start_line)
            line_hint_matched = resolved_start_line == start_line
        elif len(candidates) == 0:
            # Exact anchoring failed everywhere in range. As a robustness fallback, retry the
            # same drift search ignoring leading/trailing whitespace on both the supplied
            # anchors and the file's lines: a model that captured a line's content with its
            # surrounding indentation trimmed (or, conversely, added spurious whitespace)
            # should still resolve. Only honor this fallback when it pins down exactly one
            # location, so a genuinely wrong anchor still surfaces the precise error below.
            fuzzy_candidates = self._find_drift_candidates(
                all_lines, start_line=start_line, end_line=end_line, start_text=start_text,
                end_text=end_text, context_before=context_before, context_after=context_after,
                fuzzy=True)
            if len(fuzzy_candidates) == 1:
                fuzzy = True
                resolved_start_line = fuzzy_candidates[0]
                resolved_end_line = resolved_start_line + (end_line - start_line)
                line_hint_matched = resolved_start_line == start_line
                # Determine which anchor(s) actually needed whitespace tolerance at the
                # resolved location (the other may have matched exactly and only failed the
                # exact search because its partner didn't).
                start_fuzzy = all_lines[resolved_start_line - 1] != start_text
                end_fuzzy = all_lines[resolved_end_line - 1] != end_text
                if start_fuzzy and end_fuzzy:
                    which = "start_text and end_text were"
                    anchor = "their anchors"
                elif start_fuzzy:
                    which = "start_text was"
                    anchor = "its anchor"
                else:
                    which = "end_text was"
                    anchor = "its anchor"
                whitespace_message = (
                    f"{which} matched to {anchor} by ignoring leading/trailing whitespace; "
                    "for most accurate matching, be precise with leading and trailing whitespace.")
            else:
                if all_lines[start_line - 1] != start_text:
                    raise ValueError(
                        f"start_text does not match the content of start_line (line "
                        f"#{start_line}): {all_lines[start_line - 1]!r} != {start_text!r}, and "
                        f"no matching location was found within {self._drift_search_radius} "
                        f"lines; {reread_hint}. Line #{start_line}'s contents are "
                        f"\"{all_lines[start_line - 1]}\"")
                if all_lines[end_line - 1] != end_text:
                    raise ValueError(
                        f"end_text does not match the content of end_line (line #{end_line}): "
                        f"{all_lines[end_line - 1]!r} != {end_text!r}, and no matching "
                        f"location was found within {self._drift_search_radius} lines; "
                        f"{reread_hint}. Line #{end_line}'s contents are "
                        f"\"{all_lines[end_line - 1]}\"")
                raise ValueError(
                    f"start_text/end_text match line {start_line}..{end_line}, but the "
                    "supplied context_before/context_after does not match the surrounding "
                    f"lines there (and no other nearby location within "
                    f"{self._drift_search_radius} lines satisfies every constraint); omit or "
                    f"correct context_before/context_after, or {reread_hint} to confirm the "
                    "surrounding lines")
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
                "disambiguate -- note that \"\" there means \"assert nothing is on that "
                "side\" (e.g. genuinely the file's first/last line), which is different "
                "from omitting the argument entirely (\"don't check that side\")")

        new_lines = (
            all_lines[:resolved_start_line - 1] + new_text.splitlines() + all_lines[resolved_end_line:])
        return LineRangeEdit(
            resolved_start_line=resolved_start_line, resolved_end_line=resolved_end_line,
            line_hint_matched=line_hint_matched, new_lines=new_lines,
            fuzzy_whitespace_match=fuzzy, whitespace_message=whitespace_message)

    def _describe_candidate_neighbors(
        self, all_lines: list[str], candidate_start: int, span: int, preview_lines: int,
    ) -> str:
        """One-line description of a drift-search candidate's actual nearby lines (up to
        `preview_lines` on each side, fewer near a file's start/end), for the "Ambiguous
        match" error: naming the exact `context_before`/`context_after` *value* that singles
        out this candidate lets a model copy it verbatim rather than reconstruct it from a
        separate read call and risk miscounting which lines are truly adjacent (the same
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
        fuzzy: bool = False,
    ) -> list[int]:
        """Return every start-line position within `self._drift_search_radius` of `start_line`
        (inclusive, clipped to the file's bounds) where `start_text` occurs and `end_text`
        occurs `end_line - start_line` lines later — preserving the caller's requested span
        length — and, if `context_before`/`context_after` are given, where those also match
        (see `_context_matches_candidate`). `start_line` itself (zero drift) is an ordinary
        candidate, not special-cased, so an undrifted hint naturally resolves as the sole
        match. Candidates are returned in ascending line-number order.

        With `fuzzy=True`, the `start_text`/`end_text` comparison ignores leading/trailing
        whitespace on both the anchor and the file's line (`_anchor_matches`); the
        `context_before`/`context_after` check is still exact, since those are an optional
        disambiguation aid a caller supplies deliberately rather than an anchor likely to be
        captured with stray whitespace.
        """
        total_lines = len(all_lines)
        span = end_line - start_line
        radius = self._drift_search_radius
        lo = max(1, start_line - radius)
        hi = min(total_lines - span, start_line + radius)

        candidates: list[int] = []
        for candidate_start in range(lo, hi + 1):
            candidate_end = candidate_start + span
            if not self._anchor_matches(all_lines[candidate_start - 1], start_text, fuzzy):
                continue
            if not self._anchor_matches(all_lines[candidate_end - 1], end_text, fuzzy):
                continue
            if not self._context_matches_candidate(
                all_lines, candidate_start=candidate_start, candidate_end=candidate_end,
                context_before=context_before, context_after=context_after,
            ):
                continue
            candidates.append(candidate_start)
        return candidates

    @staticmethod
    def _anchor_matches(line: str, text: str, fuzzy: bool) -> bool:
        """True if `line` matches the supplied `text` anchor. Exact by default; with
        `fuzzy=True`, leading/trailing whitespace is ignored on both sides, so an anchor
        captured with its indentation trimmed (or with spurious surrounding whitespace) still
        matches."""
        if not fuzzy:
            return line == text
        return line.strip() == text.strip()

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
