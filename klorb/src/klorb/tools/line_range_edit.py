# © Copyright 2026 Aaron Kimball
"""Shared row-extent line-replacement mechanic behind `EditFileTool` and `EditScratchpadTool`:
locating a possibly-drifted `[start_line, end_line]` span via `start_text`/`end_text`/
`context_before`/`context_after` and substituting `new_text` for it, tolerating up to
`drift_search_radius` lines of drift the same way `EditFileTool` always has -- see
`klorb.tools.edit_file.EditFileTool` for the full user-facing contract this is the shared core
of. Factored out so `klorb.tools.scratchpad.edit.EditScratchpadTool` gets the identical
drift-tolerant substitution mechanic without duplicating it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class LineRangeEdit:
    """Resolved outcome of one row-extent substitution against a subject's current lines (see
    `resolve_line_range_edit`): where it actually landed (`resolved_start_line`/
    `resolved_end_line`), whether that matched the caller's hint exactly with no drift
    (`line_hint_matched`), and the subject's full line list after the substitution (`new_lines`).
    """

    resolved_start_line: int
    resolved_end_line: int
    line_hint_matched: bool
    new_lines: list[str]


def resolve_line_range_edit(
    all_lines: list[str], *, start_line: int, end_line: int, start_text: str, end_text: str,
    new_text: str, context_before: str | None, context_after: str | None,
    drift_search_radius: int, subject: str, reread_hint: str,
) -> LineRangeEdit:
    """Locate the inclusive line range `[start_line, end_line]` within `all_lines` and return
    `all_lines` with that range replaced by `new_text`'s lines.

    `subject` names the thing being edited for the empty-subject error message (e.g. a
    filename, or `"the scratchpad"`). `reread_hint` is substituted into every raised
    `ValueError`'s suggestion for what to do next (e.g. `"re-ReadFile foo.py"` or
    `"re-ReadScratchpad your scratchpad"`), so the message stays accurate for whichever caller
    (`EditFileTool`/`EditScratchpadTool`) invoked this.

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

    candidates = _find_drift_candidates(
        all_lines, start_line=start_line, end_line=end_line, start_text=start_text,
        end_text=end_text, context_before=context_before, context_after=context_after,
        drift_search_radius=drift_search_radius)

    if len(candidates) == 1:
        resolved_start_line = candidates[0]
        resolved_end_line = resolved_start_line + (end_line - start_line)
        line_hint_matched = resolved_start_line == start_line
    elif len(candidates) == 0:
        if all_lines[start_line - 1] != start_text:
            raise ValueError(
                f"start_text does not match line {start_line}'s actual content: "
                f"{all_lines[start_line - 1]!r} != {start_text!r}, and no matching "
                f"location was found within {drift_search_radius} lines; {reread_hint}")
        if all_lines[end_line - 1] != end_text:
            raise ValueError(
                f"end_text does not match line {end_line}'s actual content: "
                f"{all_lines[end_line - 1]!r} != {end_text!r}, and no matching "
                f"location was found within {drift_search_radius} lines; {reread_hint}")
        raise ValueError(
            f"start_text/end_text match line {start_line}..{end_line}, but the "
            "supplied context_before/context_after does not match the surrounding "
            f"lines there (and no other nearby location within "
            f"{drift_search_radius} lines satisfies every constraint); omit or "
            f"correct context_before/context_after, or {reread_hint} to confirm the "
            "surrounding lines")
    else:
        span = end_line - start_line
        preview_lines = _minimal_disambiguating_window(all_lines, candidates, span)
        descriptions = "; ".join(
            _describe_candidate_neighbors(all_lines, candidate, span, preview_lines)
            for candidate in candidates)
        raise ValueError(
            f"Ambiguous match: start_text/end_text for start_line={start_line}, "
            f"end_line={end_line} matches at {len(candidates)} locations within "
            f"{drift_search_radius} lines (lines {candidates}): {descriptions}. "
            "Retry with a start_line matching the intended location, or with the "
            "context_before/context_after values shown above for that location, to "
            "disambiguate -- note that \"\" there means \"assert nothing is on that "
            "side\" (e.g. genuinely the file's first/last line), which is different "
            "from omitting the argument entirely (\"don't check that side\")")

    new_lines = (
        all_lines[:resolved_start_line - 1] + new_text.splitlines() + all_lines[resolved_end_line:])
    return LineRangeEdit(
        resolved_start_line=resolved_start_line, resolved_end_line=resolved_end_line,
        line_hint_matched=line_hint_matched, new_lines=new_lines)


def _describe_candidate_neighbors(
    all_lines: list[str], candidate_start: int, span: int, preview_lines: int,
) -> str:
    """One-line description of a drift-search candidate's actual nearby lines (up to
    `preview_lines` on each side, fewer near a file's start/end), for the "Ambiguous match"
    error: naming the exact `context_before`/`context_after` *value* that singles out this
    candidate lets a model copy it verbatim rather than reconstruct it from a separate read
    call and risk miscounting which lines are truly adjacent (the same off-by-one mistake
    drift-tolerant relocation itself is meant to route around, just relocated into
    `context_before`/`context_after` instead of `start_line`/`end_line`). At a file's start/end,
    where there's nothing to show, the description says so explicitly and recommends the empty
    string `""`, which -- unlike omitting the argument -- asserts there's genuinely nothing on
    that side (see `_context_matches_candidate`). `preview_lines` comes from
    `_minimal_disambiguating_window`, not a fixed constant, since one line is not always enough.
    """
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
    all_lines: list[str], candidates: list[int], span: int,
) -> int:
    """Smallest number of context lines (on each side) such that every candidate in
    `candidates` is uniquely singled out by copying its own natural surrounding content as
    `context_before`/`context_after` (see `_candidate_resolved_at`) -- expanding from 1 upward.
    A single line is not always enough: two candidates can share the same immediate neighbor
    (e.g. the first two of three identical lines both have another identical line right after
    them) while still being distinguishable a line or two further out.

    This always terminates with every candidate resolved, at the latest once expansion reaches
    a candidate's own true maximal before/after line counts (all the way to the file's start
    and end): at that point no other candidate can share the same pair of counts (a different
    line can't be the same distance from both the file's start and its end), so
    `_context_matches_candidate`'s bounds checks alone rule out every other candidate there,
    independent of content.
    """
    n = 1
    while not all(
        _candidate_resolved_at(all_lines, candidate, candidates, span, n)
        for candidate in candidates
    ):
        n += 1
    return n


def _candidate_resolved_at(
    all_lines: list[str], candidate: int, candidates: list[int], span: int, n: int,
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
        if _context_matches_candidate(
            all_lines, candidate_start=other, candidate_end=other + span,
            context_before=context_before, context_after=context_after)
    ]
    return matches == [candidate]


def _find_drift_candidates(
    all_lines: list[str], *, start_line: int, end_line: int, start_text: str,
    end_text: str, context_before: str | None, context_after: str | None,
    drift_search_radius: int,
) -> list[int]:
    """Return every start-line position within `drift_search_radius` of `start_line` (inclusive,
    clipped to the file's bounds) where `start_text` occurs and `end_text` occurs
    `end_line - start_line` lines later -- preserving the caller's requested span length -- and,
    if `context_before`/`context_after` are given, where those also match (see
    `_context_matches_candidate`). `start_line` itself (zero drift) is an ordinary candidate,
    not special-cased, so an undrifted hint naturally resolves as the sole match. Candidates
    are returned in ascending line-number order.
    """
    total_lines = len(all_lines)
    span = end_line - start_line
    lo = max(1, start_line - drift_search_radius)
    hi = min(total_lines - span, start_line + drift_search_radius)

    candidates: list[int] = []
    for candidate_start in range(lo, hi + 1):
        candidate_end = candidate_start + span
        if all_lines[candidate_start - 1] != start_text:
            continue
        if all_lines[candidate_end - 1] != end_text:
            continue
        if not _context_matches_candidate(
            all_lines, candidate_start=candidate_start, candidate_end=candidate_end,
            context_before=context_before, context_after=context_after,
        ):
            continue
        candidates.append(candidate_start)
    return candidates


def _context_matches_candidate(
    all_lines: list[str], *, candidate_start: int, candidate_end: int,
    context_before: str | None, context_after: str | None,
) -> bool:
    """True if `context_before`'s lines (top-to-bottom, ending immediately before
    `candidate_start`) and `context_after`'s lines (top-to-bottom, starting immediately after
    `candidate_end`) match the file's actual content there. `None` (the argument omitted
    entirely) skips that side's check; `""` (explicitly given as empty) instead asserts there
    must be *nothing* on that side -- `candidate_start`/`candidate_end` must be the file's
    actual first/last line -- since an omitted argument and an explicit empty string are
    otherwise indistinguishable but mean opposite things ("don't care" vs. "must be the
    start/end of the file"). A candidate whose required non-empty context window would run off
    the file's start/end fails the match -- a file too short near one edge is an ordinary
    reason a candidate is wrong, not a malformed request.
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
