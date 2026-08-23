# © Copyright 2026 Aaron Kimball
"""Text-anchored block-substitution mechanic for editing files and scratchpads."""

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from klorb.tools.tool import NO_READFILE_VERIFICATION_NOTE
from klorb.tools.util.diff_lines import DiffHunk, build_diff_hunks, format_diff_hunks
from klorb.tools.util.response_headers import format_header_lines
from klorb.tools.util.secret_redaction import SecretRedactor

if TYPE_CHECKING:
    from klorb.session import Session

logger = logging.getLogger(__name__)

_EDIT_RESULT_HEADER_ORDER = (
    "namespace", "filename",
    "edit_success", "created",
    "start_line", "end_line", "replaced_lines", "new_total_lines",
    "fuzzy_whitespace_match", "whitespace", "warning", "note",
)
"""Key order `format_edit_result()` renders an `EditFileCore.apply()`-shaped result dict's
header lines in."""


def format_edit_result(result: dict[str, Any]) -> str:
    """Render an `EditFileCore.apply()`-shaped result dict as `key: value` header lines in
    `_EDIT_RESULT_HEADER_ORDER`, followed by `post_edit_content` and `diff` as separate
    plain-text blocks, each set off by a blank line."""
    header_lines = format_header_lines(
        result, _EDIT_RESULT_HEADER_ORDER,
        known_elsewhere=frozenset({"post_edit_content", "diff"}))
    post_edit_content: str = result.get("post_edit_content", "")
    diff_hunks = [DiffHunk.model_validate(hunk) for hunk in result.get("diff", [])]
    diff_block = format_diff_hunks(diff_hunks)
    return "\n\n".join(["\n".join(header_lines), post_edit_content, diff_block])


_FUZZY_TRANSLATION = str.maketrans({
    "—": "-", "–": "-", "−": "-",
    "“": '"', "”": '"',
    "‘": "'", "’": "'",
})
"""Character classes the fuzzy fallback treats as equivalent when an exact match fails: em/en
dash and minus sign as a plain hyphen, curly double quotes as `"`, curly single
quotes/apostrophes as `'`."""

_FUZZY_WHITESPACE_MESSAGE = (
    "was matched by ignoring leading/trailing whitespace and normalizing dash/quote "
    "punctuation variants; for exact matching next time, use the file's precise characters.")


@dataclass(frozen=True)
class LineRangeEdit:
    """Resolved outcome of one block substitution against a subject's current lines: where it
    landed (`resolved_start_line`/`resolved_end_line`, 1-indexed inclusive) and the subject's
    full line list after the substitution (`new_lines`).

    `fuzzy_whitespace_match` is True when the anchor(s) could only be located by ignoring
    leading/trailing whitespace and dash/quote punctuation variants after an exact match
    failed; `whitespace_message` then carries the advisory string `apply()` echoes back to the
    caller (`None` when no fuzzy fallback occurred).
    """

    resolved_start_line: int
    resolved_end_line: int
    new_lines: list[str]
    fuzzy_whitespace_match: bool = False
    whitespace_message: str | None = None


@dataclass(frozen=True)
class _NormalizedEditArgs:
    """Resolved output of `EditFileCore._normalize_edit_args()`: exactly one of `old_text` or
    the `old_text_start`/`old_text_end` pair is set (the other(s) `None`), alongside `new_text`."""

    old_text: str | None
    old_text_start: str | None
    old_text_end: str | None
    new_text: str


class EditFileCore:
    """Replaces a block of `path`'s current content with `new_text`, locating that block by an
    exact text match (falling back to a whitespace/punctuation-tolerant one) rather than a line
    number. See docs/specs/tool-framework.md for the full user-facing contract.

    Two mutually exclusive forms locate the block, both required to align on whole lines (never
    a sub-line fragment, since matching is always against complete file lines):

    * `old_text` alone -- the entire replacement block, verbatim. Must match exactly one
      location in the subject.
    * `old_text_start`/`old_text_end` together -- each must itself match exactly one location;
      everything from `old_text_start`'s match through `old_text_end`'s match, inclusive, is
      replaced by `new_text`.

    Either form raises `ValueError` naming ready-to-use candidate JSON fragments when more than
    one location matches: each candidate extends the anchor(s) with more surrounding context
    (grown outward until every candidate is uniquely distinguishable) and recapitulates that
    same extra context, unchanged, in its `new_text`.
    """

    def parameter_properties(self) -> dict[str, Any]:
        """Return the JSON-schema properties for the edit tools' `parameters()`."""
        return {
            "old_text": {
                "type": "string",
                "description": (
                    "The exact current block to replace, verbatim. One or more complete "
                    "lines. Must be unique in the file. Use \"\" only when the file "
                    "is missing or empty, to create/populate it with new_text. Mutually "
                    "exclusive with old_text_start/old_text_end."
                ),
            },
            "old_text_start": {
                "type": "string",
                "description": (
                    "For a longer span: the exact current text (one or more complete lines) "
                    "that begins the block to replace. Paired with old_text_end. Everything "
                    "from old_text_start through old_text_end, inclusive, is replaced by "
                    "new_text. Must be unique in the file."
                ),
            },
            "old_text_end": {
                "type": "string",
                "description": (
                    "The exact current text (one or more complete lines) that ends the block "
                    "old_text_start begins. Must be unique in the file."
                ),
            },
            "new_text": {
                "type": "string",
                "description": (
                    "Full replacement content, as raw text with '\\n' between lines. May span "
                    "any number of lines, including zero (an empty string deletes the matched "
                    "range)."
                ),
            },
        }

    def apply(
        self, path: Path, args: dict[str, Any], *, subject: str, reread_hint: str, create_hint: str,
        redactor: SecretRedactor | None = None, session: "Session | None" = None,
    ) -> dict[str, Any]:
        """Apply one block substitution to `path` per `args`, returning `start_line`/`end_line`
        (the edited region, 1-indexed, renumbered to reflect `new_text`'s own line count),
        `new_total_lines`, `post_edit_content`, `edit_success`, `diff`, and `note`.

        When `redactor` is given, `old_text`/`old_text_start`/`old_text_end`/`new_text` are each
        passed through `redactor.detokenize(session, ...)` before matching or writing.
        `post_edit_content` and `diff` are then re-redacted before being returned, so the result
        never echoes a plaintext secret back either. See docs/specs/secret-redaction.md.

        `path` need not already exist when `args` uses `old_text=""`: a missing `path` is then
        treated exactly like an existing-but-empty one, `path`'s parent directories are created
        as needed, and the result gains `created: true`. For any other `args` shape, a missing
        `path` raises `FileNotFoundError` naming `create_hint` as the tool to create it with first.

        `subject` names the thing being edited, for error messages. `reread_hint` is substituted
        into every raised `ValueError`'s suggestion for what to do next, so the message stays
        accurate for whichever caller invoked this.
        """
        normalized = self._normalize_edit_args(args)
        if redactor is not None:
            normalized = self._detokenize_normalized_args(normalized, redactor, session)
        new_text = normalized.new_text
        empty_insert = normalized.old_text == ""

        file_existed = True
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if not empty_insert:
                raise FileNotFoundError(
                    f"{subject} does not exist; use {create_hint} to create it first, then "
                    "edit it -- or, to create it directly with this call, pass old_text=\"\" "
                    "alongside new_text.") from None
            file_existed = False
            raw = ""

        original_ends_with_newline = raw.endswith("\n")
        all_lines = raw.splitlines()
        total_lines = len(all_lines)

        if empty_insert:
            if total_lines != 0:
                raise ValueError(
                    f"old_text=\"\" only matches when {subject} is missing or empty, but it "
                    f"has {total_lines} line(s); provide the exact old_text to replace.")
            edit = LineRangeEdit(
                resolved_start_line=1, resolved_end_line=0, new_lines=new_text.splitlines())
        elif normalized.old_text is not None:
            edit = self._resolve_old_text_edit(
                all_lines, old_text=normalized.old_text, new_text=new_text, subject=subject,
                reread_hint=reread_hint)
        else:
            assert normalized.old_text_start is not None
            assert normalized.old_text_end is not None
            edit = self._resolve_range_edit(
                all_lines, old_text_start=normalized.old_text_start,
                old_text_end=normalized.old_text_end, new_text=new_text, subject=subject,
                reread_hint=reread_hint)

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
        if not file_existed:
            logger.debug("EditFileCore creating %s (and any missing parent directories)", path)
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        new_total_lines = len(new_lines)
        inserted_line_count = len(new_text.splitlines())
        snippet_end = resolved_start_line + inserted_line_count - 1
        snippet_lines = new_text.splitlines()
        diff_hunks = build_diff_hunks(all_lines, new_lines)
        if redactor is not None:
            # Re-mask the just-written region for display: `content` above is the real bytes
            # that belong on disk, but nothing that echoes it back to the model should carry a
            # plaintext secret either -- see docs/specs/secret-redaction.md.
            snippet_lines = [redactor.redact(session, line) for line in snippet_lines]
            for hunk in diff_hunks:
                for diff_line in hunk.lines:
                    diff_line.text = redactor.redact(session, diff_line.text)
        snippet = "\n".join(
            f"{resolved_start_line + i}|{line}" for i, line in enumerate(snippet_lines))

        out: dict[str, Any] = {
            "start_line": resolved_start_line,
            "end_line": snippet_end,
            "replaced_lines": resolved_end_line - resolved_start_line + 1,
            "new_total_lines": new_total_lines,
            "post_edit_content": snippet,
            "edit_success": True,
            "diff": [hunk.model_dump() for hunk in diff_hunks],
            "note": NO_READFILE_VERIFICATION_NOTE,
        }
        if not file_existed:
            out["created"] = True
        if edit.fuzzy_whitespace_match:
            out["fuzzy_whitespace_match"] = True
            out["whitespace"] = edit.whitespace_message

        return out

    @staticmethod
    def _detokenize_normalized_args(
        normalized: "_NormalizedEditArgs", redactor: SecretRedactor, session: "Session | None",
    ) -> "_NormalizedEditArgs":
        """Substitute any `SecretRedactor` tokens in `normalized`'s anchors/replacement text
        back to their real plaintext, so the resolvers below match against (and `apply()`
        writes) the file's actual bytes rather than a `[[SECRET:...]]` placeholder.
        See docs/specs/secret-redaction.md."""
        return replace(
            normalized,
            old_text=(
                redactor.detokenize(session, normalized.old_text)
                if normalized.old_text is not None else None),
            old_text_start=(
                redactor.detokenize(session, normalized.old_text_start)
                if normalized.old_text_start is not None else None),
            old_text_end=(
                redactor.detokenize(session, normalized.old_text_end)
                if normalized.old_text_end is not None else None),
            new_text=redactor.detokenize(session, normalized.new_text))

    def _normalize_edit_args(self, args: dict[str, Any]) -> _NormalizedEditArgs:
        """Resolve `args` into exactly one of the two accepted forms: `old_text` alone, or
        `old_text_start`/`old_text_end` together. Raises `ValueError` naming the specific
        problem for every other combination (both forms given, only one of
        `old_text_start`/`old_text_end` given, neither form given, or a non-string/empty
        value where a non-empty string is required)."""
        try:
            new_text = args["new_text"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'new_text'. Provide the replacement text.")
        if not isinstance(new_text, str):
            raise ValueError(
                f"new_text must be a string, got {new_text!r} ({type(new_text).__name__})")

        has_old_text = "old_text" in args
        has_start = "old_text_start" in args
        has_end = "old_text_end" in args

        if has_old_text and (has_start or has_end):
            raise ValueError("Provide old_text, or old_text_start/old_text_end, not both.")
        if has_start != has_end:
            raise ValueError("old_text_start and old_text_end must be provided together.")
        if not has_old_text and not has_start:
            raise ValueError(
                "Missing required argument: provide old_text, or old_text_start/old_text_end.")

        if has_old_text:
            old_text = args["old_text"]
            if not isinstance(old_text, str):
                raise ValueError(
                    f"old_text must be a string, got {old_text!r} ({type(old_text).__name__})")
            return _NormalizedEditArgs(
                old_text=old_text, old_text_start=None, old_text_end=None, new_text=new_text)

        old_text_start = args["old_text_start"]
        old_text_end = args["old_text_end"]
        for label, value in (("old_text_start", old_text_start), ("old_text_end", old_text_end)):
            if not isinstance(value, str):
                raise ValueError(f"{label} must be a string, got {value!r} ({type(value).__name__})")
            if value == "":
                raise ValueError(f"{label} must be non-empty -- one or more complete lines.")
        return _NormalizedEditArgs(
            old_text=None, old_text_start=old_text_start, old_text_end=old_text_end,
            new_text=new_text)

    def _resolve_old_text_edit(
        self, all_lines: list[str], *, old_text: str, new_text: str, subject: str,
        reread_hint: str,
    ) -> LineRangeEdit:
        """Locate `old_text` (split into lines) as a contiguous block within `all_lines` and
        return `all_lines` with that block replaced by `new_text`'s lines. Raises `ValueError`
        if `old_text` matches nowhere, or names ready-to-use candidates if it matches more than
        one location."""
        total_lines = len(all_lines)
        block_lines = old_text.splitlines()
        block_len = len(block_lines)

        matches, used_fuzzy = self._find_unique_or_fuzzy(all_lines, block_lines)
        if not matches:
            raise ValueError(
                f"old_text does not match any location in the {total_lines}-line {subject}; "
                f"{reread_hint}")
        if len(matches) == 1:
            start = matches[0]
            end = start + block_len - 1
            new_lines = all_lines[:start - 1] + new_text.splitlines() + all_lines[end:]
            return LineRangeEdit(
                resolved_start_line=start, resolved_end_line=end, new_lines=new_lines,
                fuzzy_whitespace_match=used_fuzzy,
                whitespace_message=(f"old_text {_FUZZY_WHITESPACE_MESSAGE}" if used_fuzzy else None))

        spans = [(start, start + block_len - 1) for start in matches]
        n = self._minimal_disambiguating_n(all_lines, spans)
        raise ValueError(
            self._describe_old_text_ambiguity(all_lines, spans, new_text, n, total_lines, subject))

    def _resolve_range_edit(
        self, all_lines: list[str], *, old_text_start: str, old_text_end: str, new_text: str,
        subject: str, reread_hint: str,
    ) -> LineRangeEdit:
        """Locate `old_text_start` and `old_text_end` (each a contiguous block) within
        `all_lines`, pairing each start match with its nearest end match at or after that same
        line (so `old_text_start`/`old_text_end` naming the identical single line is a valid,
        zero-interior span). Raises `ValueError` if either anchor matches nowhere, if no
        start has any valid following end, or names ready-to-use candidates if more than one
        pairing survives."""
        total_lines = len(all_lines)
        start_lines = old_text_start.splitlines()
        end_lines = old_text_end.splitlines()
        start_len = len(start_lines)
        end_len = len(end_lines)

        start_matches, start_fuzzy = self._find_unique_or_fuzzy(all_lines, start_lines)
        if not start_matches:
            raise ValueError(
                f"old_text_start does not match any location in the {total_lines}-line "
                f"{subject}; {reread_hint}")
        end_matches, end_fuzzy = self._find_unique_or_fuzzy(all_lines, end_lines)
        if not end_matches:
            raise ValueError(
                f"old_text_end does not match any location in the {total_lines}-line "
                f"{subject}; {reread_hint}")

        valid_pairs = []
        for s in start_matches:
            following = [e for e in end_matches if e >= s]
            if following:
                valid_pairs.append((s, min(following)))
        if not valid_pairs:
            raise ValueError(
                f"old_text_start matches at line(s) {start_matches} and old_text_end matches "
                f"at line(s) {end_matches}, but old_text_end never occurs at or after "
                f"old_text_start; {reread_hint}")
        if len(valid_pairs) == 1:
            s, e = valid_pairs[0]
            span_end = e + end_len - 1
            new_lines = all_lines[:s - 1] + new_text.splitlines() + all_lines[span_end:]
            fuzzy = start_fuzzy or end_fuzzy
            return LineRangeEdit(
                resolved_start_line=s, resolved_end_line=span_end, new_lines=new_lines,
                fuzzy_whitespace_match=fuzzy,
                whitespace_message=(
                    f"old_text_start/old_text_end {_FUZZY_WHITESPACE_MESSAGE}" if fuzzy else None))

        spans = [(s, e + end_len - 1) for s, e in valid_pairs]
        n = self._minimal_disambiguating_n(all_lines, spans)
        raise ValueError(
            self._describe_range_ambiguity(
                all_lines, valid_pairs, start_len, end_len, new_text, n, total_lines, subject))

    def _find_unique_or_fuzzy(
        self, all_lines: list[str], block_lines: list[str],
    ) -> tuple[list[int], bool]:
        """Exact matches for `block_lines` within `all_lines`, falling back to whitespace/
        punctuation-tolerant matches only when the exact search finds none. The second element
        is True when the returned matches came from that fallback."""
        matches = self._find_block_matches(all_lines, block_lines, fuzzy=False)
        if matches:
            return matches, False
        fuzzy_matches = self._find_block_matches(all_lines, block_lines, fuzzy=True)
        return fuzzy_matches, bool(fuzzy_matches)

    @staticmethod
    def _find_block_matches(all_lines: list[str], block_lines: list[str], fuzzy: bool) -> list[int]:
        """Every 1-indexed start line where `block_lines` matches `all_lines` as a contiguous
        block -- exactly, or with `fuzzy=True`, after `_normalize_fuzzy` on each line."""
        total_lines = len(all_lines)
        block_len = len(block_lines)
        if block_len == 0 or block_len > total_lines:
            return []
        matches = []
        for start in range(1, total_lines - block_len + 2):
            actual = all_lines[start - 1:start - 1 + block_len]
            if EditFileCore._block_matches(actual, block_lines, fuzzy):
                matches.append(start)
        return matches

    @staticmethod
    def _block_matches(actual: list[str], expected: list[str], fuzzy: bool) -> bool:
        if not fuzzy:
            return actual == expected
        return [EditFileCore._normalize_fuzzy(a) for a in actual] == [
            EditFileCore._normalize_fuzzy(e) for e in expected]

    @staticmethod
    def _normalize_fuzzy(line: str) -> str:
        """Strip leading/trailing whitespace and fold dash/quote punctuation variants to their
        plain-ASCII equivalents, for `_block_matches`'s fallback comparison."""
        return line.strip().translate(_FUZZY_TRANSLATION)

    def _minimal_disambiguating_n(
        self, all_lines: list[str], spans: list[tuple[int, int]],
    ) -> int:
        """Smallest `n` such that extending every span in `spans` (1-indexed, inclusive) by `n`
        lines on each side (clipped to the file's bounds) would, if sent verbatim as a real
        `old_text`/`old_text_start`/`old_text_end` value, resolve unambiguously. Always
        terminates: once `n` is large enough that a span's window has clipped to the entire
        file (`lo == 0` and `hi == len(all_lines)`), that window can only ever match the file
        at its own single starting position (nothing else is the same length), so every span
        resolves by `n == len(all_lines)` at the latest. Starts at `n=1`, since every span in
        `spans` shares identical content at `n=0` by construction (that's how they became
        candidates).
        """
        n = 1
        while not all(self._span_resolved_at(all_lines, span, n) for span in spans):
            n += 1
        return n

    def _span_resolved_at(self, all_lines: list[str], span: tuple[int, int], n: int) -> bool:
        """True if extending `span` by `n` lines on each side (clipped to the file's bounds)
        produces a block that matches `all_lines` only at that extended window's own position."""
        lo, hi = self._span_bounds(all_lines, span, n)
        window = all_lines[lo:hi]
        return self._find_block_matches(all_lines, window, fuzzy=False) == [lo + 1]

    @staticmethod
    def _span_bounds(all_lines: list[str], span: tuple[int, int], n: int) -> tuple[int, int]:
        """0-indexed half-open `(lo, hi)` bounds of `span` (1-indexed, inclusive) extended by
        `n` lines on each side, clipped to `all_lines`'s bounds."""
        start, end = span
        lo = max(0, start - 1 - n)
        hi = min(len(all_lines), end + n)
        return lo, hi

    @staticmethod
    def _describe_old_text_ambiguity(
        all_lines: list[str], spans: list[tuple[int, int]], new_text: str, n: int,
        total_lines: int, subject: str,
    ) -> str:
        """Render the "Ambiguous match" `ValueError` message for `_resolve_old_text_edit`: one
        ready-to-use `{old_text, new_text}` JSON fragment per candidate in `spans`, each
        extended by `n` lines of surrounding context on both sides and with that same context
        recapitulated, unchanged, in the candidate's `new_text`."""
        fragments = []
        for start, end in spans:
            lo, hi = EditFileCore._span_bounds(all_lines, (start, end), n)
            old_text_candidate = "\n".join(all_lines[lo:hi])
            new_text_candidate = "\n".join(
                all_lines[lo:start - 1] + new_text.splitlines() + all_lines[end:hi])
            fragments.append(
                f"For the match at line {start}: " + json.dumps(
                    {"old_text": old_text_candidate, "new_text": new_text_candidate}))
        match_lines = ", ".join(str(start) for start, _ in spans)
        return (
            f"Ambiguous match: old_text matches {len(spans)} locations in the "
            f"{total_lines}-line {subject} (lines {match_lines}):\n * " + "\n * ".join(fragments) +
            "\nRetry with one of the exact json fragments shown above (old_text extended with "
            "more surrounding context, new_text recapitulating that context unchanged) to "
            "choose a target.")

    @staticmethod
    def _describe_range_ambiguity(
        all_lines: list[str], valid_pairs: list[tuple[int, int]], start_len: int, end_len: int,
        new_text: str, n: int, total_lines: int, subject: str,
    ) -> str:
        """Render the "Ambiguous match" `ValueError` message for `_resolve_range_edit`: one
        ready-to-use `{old_text_start, old_text_end, new_text}` JSON fragment per surviving
        `(start, end)` pairing, `old_text_start` extended backward and `old_text_end` extended
        forward by `n` lines, with that same extra context recapitulated, unchanged, at the
        matching end of the candidate's `new_text`."""
        fragments = []
        for s, e in valid_pairs:
            span_end = e + end_len - 1
            lo, hi = EditFileCore._span_bounds(all_lines, (s, span_end), n)
            old_text_start_candidate = "\n".join(all_lines[lo:s - 1 + start_len])
            old_text_end_candidate = "\n".join(all_lines[e - 1:hi])
            new_text_candidate = "\n".join(
                all_lines[lo:s - 1] + new_text.splitlines() + all_lines[span_end:hi])
            fragments.append(
                f"For the match spanning lines {s}-{span_end}: " + json.dumps({
                    "old_text_start": old_text_start_candidate,
                    "old_text_end": old_text_end_candidate,
                    "new_text": new_text_candidate,
                }))
        return (
            f"Ambiguous match: old_text_start/old_text_end together match {len(valid_pairs)} "
            f"locations in the {total_lines}-line {subject}:\n * " + "\n * ".join(fragments) +
            "\nRetry with one of the exact json fragments shown above (old_text_start/"
            "old_text_end extended with more surrounding context, new_text recapitulating that "
            "context unchanged) to choose a target.")
