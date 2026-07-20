# © Copyright 2026 Aaron Kimball
"""The drift-tolerant row-extent substitution mechanic shared by `EditFileTool` and
`EditScratchpadTool` — see `klorb.tools.util`'s package docstring for how the two tools each
hold one of these as a member and delegate to it."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from klorb.tools.util.diff_lines import build_diff_hunks

logger = logging.getLogger(__name__)

LINE_HINT_ALIASES = ("line", "line_num", "line_no", "line_number")
"""Alternate spellings for the line hint, accepted in every accepted argument form (classic,
single-line shortcut, or `old_text`) -- see `EditFileCore._normalize_edit_args`. `start_line` is
always the primary, schema-advertised spelling; these are a tolerance net for when a model
reaches for a different name."""


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


@dataclass(frozen=True)
class _NormalizedEditArgs:
    """Resolved output of `EditFileCore._normalize_edit_args()`: the concrete
    `start_line`/`end_line`/`start_text`/`end_text` a row-extent substitution operates on, no
    matter which accepted argument form (classic, single-line shortcut, or `old_text`) the
    caller used -- see docs/specs/tool-framework.md for the full accepted-argument
    matrix. `block_lines` carries `old_text`'s full split lines for full-block drift
    verification (`None` outside `old_text` mode); `alias_used` and `form6_conversion` drive
    the advisory `feedback` `apply()` emits for a form the caller may not have used on purpose.
    `unbounded` is True when `old_text` was given with no line hint at all: `start_line`/
    `end_line` are seeded to `1`/`len(block_lines)` so the rest of the pipeline has concrete
    coordinates, but the drift search ignores `drift_search_radius` and scans the whole subject
    instead of a window around that seed.
    """

    start_line: int
    end_line: int
    start_text: str
    end_text: str
    new_text: str
    context_before: str | None
    context_after: str | None
    block_lines: list[str] | None
    alias_used: str | None
    form6_conversion: bool
    unbounded: bool


class EditFileCore:
    """Replaces the inclusive line range `[start_line, end_line]` of `path` with `new_text`,
    after locating `start_text`/`end_text` (or a verbatim `old_text` block) at (or near) those
    lines, tolerating up to `drift_search_radius` lines of drift — the shared mechanic behind
    `EditFileTool` and `EditScratchpadTool`. See `klorb.tools.edit_file.EditFileTool` and
    docs/specs/tool-framework.md for the full user-facing contract this implements,
    including the widened argument matrix (`old_text`, the single-line shortcut, and the
    `line`/`line_num`/`line_no`/`line_number` hint aliases) `_normalize_edit_args()` accepts.
    """

    def __init__(self, drift_search_radius: int) -> None:
        self._drift_search_radius = drift_search_radius

    def parameter_properties(self) -> dict[str, Any]:
        """Return the JSON-schema properties shared by `EditFileTool` and `EditScratchpadTool`'s
        `parameters()` — each adds its own `filename` property (or not) and `required` list
        around this. Kept terse: the prose teaching an agent *when* to prefer each accepted
        form lives in the system prompt's edit examples, not here, since this schema is inlined
        into every edit tool's definition and paid for on every turn."""
        return {
            "start_line": {
                "type": "integer",
                "description": "1-indexed line to start replacing from.",
            },
            "end_line": {
                "type": "integer",
                "description": (
                    "1-indexed, inclusive line to stop replacing at. Optional in some forms "
                    "-- see system prompt."
                ),
            },
            "old_text": {
                "type": "string",
                "description": (
                    "The entire current block to replace, verbatim. "
                    "RECOMMENDED method when editing 1—5 lines. "
                    "`end_line` is optional (inferred from its line count). A line "
                    "hint (start_line or an alias) is also optional here -- omit it entirely "
                    "to search the whole file for old_text."
                ),
            },
            "start_text": {
                "type": "string",
                "description": (
                    "Use start_text/end_text for longer passages when old_text would "
                    "use too many tokens. "
                    "The CURRENT content at start_line -- one line, verbatim, no "
                    "trailing newline. For verifying you're editing the right spot. "
                    "Optional in some forms -- see system prompt."
                ),
            },
            "end_text": {
                "type": "string",
                "description": (
                    "The CURRENT content at end_line -- one line, verbatim, no "
                    "trailing newline. Same rule as start_text. Optional in some forms -- "
                    "see system prompt."
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
            "line": {"type": "integer"},
            "line_num": {"type": "integer"},
            "line_no": {"type": "integer"},
            "line_number": {"type": "integer"},
            "context_before": {
                "type": "string",
                "description": (
                    "Optional. Raw content (no line numbers) immediately before start_line, "
                    "used to disambiguate multiple matches. Only include "
                    "after an 'Ambiguous match' error. If start_line "
                    "is genuinely the file's first line, use context_before_start=true instead "
                    "of trying to send an empty string here. Include enough lines before/after "
                    "to *uniquely* identify the match region."
                ),
            },
            "context_before_start": {
                "type": "boolean",
                "description": (
                    "Set true to assert start_line is the file's very first line -- use this "
                    "instead of context_before=\"\". Ignored if context_before is also given."
                ),
            },
            "context_after": {
                "type": "string",
                "description": (
                    "Optional. Like context_before, but matches immediately after end_line. If "
                    "end_line is genuinely the file's last line, use context_after_end=true "
                    "instead of trying to send an empty string here."
                ),
            },
            "context_after_end": {
                "type": "boolean",
                "description": (
                    "Set true to assert end_line is the file's very last line -- use this "
                    "instead of context_after=\"\". Ignored if context_after is also given."
                ),
            },
        }

    def apply(
        self, path: Path, args: dict[str, Any], *, subject: str, reread_hint: str, create_hint: str,
    ) -> dict[str, Any]:
        """Apply one row-extent substitution to `path` per `args`, returning
        `requested_start_line`, `requested_end_line`, `start_line`, `end_line`,
        `line_hint_matched`, `new_total_lines`, `content`, and `diff` -- a jsonable rendering of
        `klorb.tools.util.diff_lines.build_diff_hunks()`'s full before/after diff, for a `Tool`'s
        `diff_preview()` to parse back into `DiffHunk`s (the caller adds `filename` if it has
        one).

        `args` may take any of the forms `_normalize_edit_args()` accepts: the classic
        `start_line`/`end_line`/`start_text`/`end_text` call; the single-line shortcut
        (`start_line == end_line`, `end_text` omitted, imputed as `start_line`/`start_text` when
        `end_line` is *also* omitted -- regardless of how many lines `new_text` spans); or
        `old_text` (the whole replacement block verbatim, with `end_line` optional, and a line
        hint optional too -- omitting it searches the whole subject rather than a window near a
        hint). `requested_end_line` echoes an `end_line` inferred from `old_text` rather than
        the literal (possibly absent) argument, and `requested_start_line`/`requested_end_line`
        are `1`/`len(old_text)`'s line count when the line hint itself was omitted.

        `path` need not already exist when `args` uses the empty-subject insert shape
        (`start_line=1, end_line=0, start_text="", end_text=""`): a missing `path` is then
        treated exactly like an existing-but-empty one, `path`'s parent directories are created
        as needed (mirroring `CreateFileCore.apply()`), and the result gains `created: true`.
        For any other shape, a missing `path` raises `FileNotFoundError` naming `create_hint`
        (e.g. `"CreateFile"`) as the tool to create it with first -- this mechanic can't create
        a file at an arbitrary line range, only via that one specific empty-insert shape.

        `subject` names the thing being edited, for the empty-subject and missing-file error
        messages (e.g. a filename, or `"the scratchpad"`). `reread_hint` is substituted into
        every raised `ValueError`'s suggestion for what to do next (e.g. `"re-ReadFile foo.py"`
        or `"re-ReadScratchpad your scratchpad"`), so the message stays accurate for whichever
        caller (`EditFileTool`/`EditScratchpadTool`/`EditMemoryTool`) invoked this.
        """
        normalized = self._normalize_edit_args(args)
        start_line = normalized.start_line
        end_line = normalized.end_line
        start_text = normalized.start_text
        end_text = normalized.end_text
        new_text = normalized.new_text
        context_before = normalized.context_before
        context_after = normalized.context_after
        block_lines = normalized.block_lines
        unbounded = normalized.unbounded

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

        # A missing path is only ever recoverable via the empty-subject insert shape (see
        # _resolve_line_range_edit's own start_line=1/end_line=0 handling): treat it exactly
        # like an existing-but-empty file there, so a caller who already knows there's nothing
        # to anchor on doesn't have to make a separate CreateFile/CreateMemory call first. Any
        # other shape can't create a file at an arbitrary line range, so it fails closed with a
        # message naming the right tool to create it with -- not the bare OS errno text.
        file_existed = True
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            if not (start_line == 1 and end_line == 0):
                raise FileNotFoundError(
                    f"{subject} does not exist; use {create_hint} to create it first, then "
                    "edit it -- or, to create it directly with this call, pass start_line=1, "
                    "end_line=0, start_text=\"\", end_text=\"\" alongside new_text.") from None
            file_existed = False
            raw = ""
        original_ends_with_newline = raw.endswith("\n")
        all_lines = raw.splitlines()
        total_lines = len(all_lines)

        edit = self._resolve_line_range_edit(
            all_lines, start_line=start_line, end_line=end_line, start_text=start_text,
            end_text=end_text, new_text=new_text, context_before=context_before,
            context_after=context_after, subject=subject, reread_hint=reread_hint,
            block_lines=block_lines, unbounded=unbounded)
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
        snippet = "\n".join(
            f"{resolved_start_line + i}|{line}" for i, line in enumerate(new_text.splitlines()))
        diff_hunks = build_diff_hunks(all_lines, new_lines)

        out: dict[str, Any] = {
            "requested_start_line": start_line,
            "requested_end_line": end_line,
            "start_line": resolved_start_line,
            "end_line": snippet_end,
            "line_hint_matched": edit.line_hint_matched,
            "new_total_lines": new_total_lines,
            "post_edit_content": snippet,
            "edit_success": True,
            "diff": [hunk.model_dump() for hunk in diff_hunks],
        }
        if not file_existed:
            out["created"] = True

        # When the anchors could only be located by ignoring leading/trailing whitespace,
        # surface that so the caller knows to be more precise next time.
        if edit.fuzzy_whitespace_match:
            out["fuzzy_whitespace_match"] = True
            out["whitespace"] = edit.whitespace_message

        # Advisory feedback, never an error: only for a form the harness reinterpreted in a way
        # the caller may not have intended (truncation, the implicit start_text -> old_text
        # conversion, an alias spelling). A clean use of any first-class form gets none.
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
        if normalized.form6_conversion:
            user_feedback.append(
                "btw, start_text held multiple lines with no end_text, so it was treated as "
                "old_text; you can pass old_text directly next time.")
        if normalized.alias_used is not None:
            user_feedback.append(
                f"the line hint is start_line; {normalized.alias_used!r} was accepted this time.")
        if len(user_feedback):
            out['feedback'] = user_feedback

        return out

    @staticmethod
    def _require_int(label: str, value: Any) -> int:
        """Type-check one line-number-shaped argument (`start_line`/`end_line`/a hint alias),
        raising the same style of `ValueError` `apply()` has always raised for a non-integer
        line number."""
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{label} must be an integer, got {value!r} ({type(value).__name__})")
        return value

    @staticmethod
    def _require_bool(label: str, value: Any) -> bool:
        """Type-check one boolean-shaped argument (`context_before_start`/`context_after_end`),
        raising a `ValueError` naming the offending argument for a non-boolean value."""
        if not isinstance(value, bool):
            raise ValueError(f"{label} must be a boolean, got {value!r} ({type(value).__name__})")
        return value

    def _normalize_edit_args(self, args: dict[str, Any]) -> _NormalizedEditArgs:
        """Resolve `args` into concrete `start_line`/`end_line`/`start_text`/`end_text` per the
        accepted-argument matrix in docs/specs/tool-framework.md: the classic
        five-field call; the single-line shortcut (`start_line == end_line`, `end_text`
        omitted/empty); and `old_text` (the whole replacement block verbatim, `end_line`
        inferred from its line count unless supplied and cross-checked), including its line-hint
        aliases (`line`/`line_num`/`line_no`/`line_number`, accepted in every form) and the
        implicit conversion of a multi-line `start_text` with no `end_text` into `old_text`.
        Raises `ValueError` naming the specific problem for every other combination -- see that
        spec's "The accepted argument matrix" section for the exact conditions.
        """
        try:
            new_text = args["new_text"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'new_text'. Provide the replacement text for the "
                "start_line..end_line range.")
        context_before = args.get("context_before")
        context_after = args.get("context_after")
        # context_before_start/context_after_end are boolean sentinels for "the empty string":
        # models reliably fumble sending a genuinely empty context_before/context_after (often
        # emitting bare, unquoted whitespace in the tool-call JSON instead), so these give the
        # same "genuinely nothing on that side" assertion a shape that's easier to send
        # correctly. Only consulted when the corresponding context_*/context_*_end wasn't given
        # at all -- an explicit context_before/context_after (even "") always wins.
        if context_before is None and "context_before_start" in args:
            if self._require_bool("context_before_start", args["context_before_start"]):
                context_before = ""
        if context_after is None and "context_after_end" in args:
            if self._require_bool("context_after_end", args["context_after_end"]):
                context_after = ""

        has_old_text = "old_text" in args
        old_text = args.get("old_text")
        has_start_text = "start_text" in args
        start_text = args.get("start_text")
        has_end_text = "end_text" in args
        end_text = args.get("end_text")

        if has_old_text:
            if not isinstance(old_text, str):
                raise ValueError(
                    f"old_text must be a string, got {old_text!r} ({type(old_text).__name__})")
            if old_text == "":
                raise ValueError(
                    "old_text must be the non-empty block to replace; to insert into an empty "
                    "file use start_line=1, end_line=0, start_text=\"\", end_text=\"\"")
            start_text_meaningful = has_start_text and start_text not in (None, "")
            end_text_meaningful = has_end_text and end_text not in (None, "")
            if start_text_meaningful or end_text_meaningful:
                raise ValueError("Provide old_text, or start_text/end_text, not both.")

        # Form 6: a multi-line start_text with no end_text is reinterpreted as old_text, so an
        # agent that pasted the whole block into start_text still gets a useful edit rather than
        # the classic truncate-to-first-line behavior (which stays reserved for when end_text is
        # also supplied -- see docs/adrs/edit-file-form6-only-converts-without-end-text.md).
        form6_conversion = False
        if (
            not has_old_text and has_start_text and isinstance(start_text, str) and "\n" in start_text
            and (not has_end_text or end_text in (None, ""))
        ):
            form6_conversion = True
            has_old_text = True
            old_text = start_text
            has_start_text = False
            start_text = None
            has_end_text = False
            end_text = None

        old_text_mode = has_old_text

        hint_candidates: list[tuple[str, int]] = []
        if "start_line" in args:
            hint_candidates.append(("start_line", self._require_int("start_line", args["start_line"])))
        for alias in LINE_HINT_ALIASES:
            if alias in args:
                hint_candidates.append((alias, self._require_int(alias, args[alias])))

        unbounded = False
        if not hint_candidates:
            # No line hint at all is only tolerable in old_text mode: there's a verbatim block
            # to anchor on, so the drift search can scan the whole subject instead of a window
            # around a hint that was never given -- see the `unbounded` field's docstring.
            if not old_text_mode:
                raise ValueError(
                    "Missing required argument: provide 'start_line' (or one of "
                    "'line'/'line_num'/'line_no'/'line_number') naming the 1-based line where "
                    "the edit begins.")
            if "end_line" in args:
                raise ValueError(
                    "end_line requires a line hint (start_line, or one of "
                    "'line'/'line_num'/'line_no'/'line_number') to anchor against; omit "
                    "end_line too to search the whole subject for old_text, or provide "
                    "start_line.")
            unbounded = True
            start_line = 1
            alias_used = None
        else:
            first_name, first_value = hint_candidates[0]
            conflict = next(
                ((name, value) for name, value in hint_candidates[1:] if value != first_value), None)
            if conflict is not None:
                conflict_name, conflict_value = conflict
                raise ValueError(
                    f"Conflicting line hints: {first_name}={first_value} vs "
                    f"{conflict_name}={conflict_value}.")
            start_line = first_value
            alias_used = None if "start_line" in args else next(
                (name for name, _ in hint_candidates if name != "start_line"), None)

        block_lines: list[str] | None
        if old_text_mode:
            assert isinstance(old_text, str)
            old_text_lines = old_text.splitlines()
            block_len = len(old_text_lines)
            start_text = old_text_lines[0]
            end_text = old_text_lines[-1]
            block_lines = old_text_lines
            if "end_line" in args:
                given_end_line = self._require_int("end_line", args["end_line"])
                if given_end_line - start_line + 1 != block_len:
                    raise ValueError(
                        f"old_text has {block_len} line(s), but end_line={given_end_line} with "
                        f"start_line={start_line} implies a "
                        f"{given_end_line - start_line + 1}-line span; drop end_line to let it "
                        "be inferred from old_text, or fix the mismatch.")
                end_line = given_end_line
            else:
                end_line = start_line + block_len - 1
        else:
            block_lines = None
            if not has_start_text:
                raise ValueError("No anchor: provide start_text (+end_text) or old_text.")
            if not isinstance(start_text, str):
                raise ValueError(
                    f"start_text must be a string, got {start_text!r} "
                    f"({type(start_text).__name__})")
            end_text_meaningful = has_end_text and end_text not in (None, "")
            if end_text_meaningful:
                if not isinstance(end_text, str):
                    raise ValueError(
                        f"end_text must be a string, got {end_text!r} "
                        f"({type(end_text).__name__})")
                if "end_line" not in args:
                    raise ValueError(
                        "Missing required argument: 'end_line'. Provide the 1-based line "
                        "number where the edit ends.")
                end_line = self._require_int("end_line", args["end_line"])
            else:
                if "end_line" not in args:
                    # No end_text and no end_line at all reads as single-line intent: a bare
                    # start_text anchor unambiguously names the one line being replaced,
                    # regardless of how many lines new_text spans (a single line growing into
                    # several is an ordinary insert, same as the classic single-line shortcut
                    # with end_line supplied explicitly) -- so impute the other half of the
                    # shortcut rather than making the caller repeat start_line as end_line.
                    end_line = start_line
                    end_text = start_text
                else:
                    end_line = self._require_int("end_line", args["end_line"])
                    # Carve-out for the legacy empty-subject insert convention (start_line=1,
                    # end_line=0, start_text=""): that exact pair is deliberately not a single
                    # line (end_line < start_line), so it's exempt from the equality check below
                    # -- `_resolve_line_range_edit`'s own empty-subject branch validates it.
                    # Keyed on the exact start_line/end_line pair (not merely on
                    # start_text/end_text being empty) so an unrelated multi-line call with blank
                    # anchors at its endpoints can't slip through this carve-out too -- and
                    # end_text need not be present at all (as opposed to explicitly ""), since
                    # it's schema-optional like every other single-line-shortcut call.
                    is_legacy_empty_insert = start_line == 1 and end_line == 0 and start_text == ""
                    if end_line != start_line and not is_legacy_empty_insert:
                        raise ValueError(
                            "Omit-end_text is only for single-line edits (start_line == "
                            "end_line); otherwise supply end_text or use old_text.")
                    end_text = start_text

        return _NormalizedEditArgs(
            start_line=start_line, end_line=end_line, start_text=start_text, end_text=end_text,
            new_text=new_text, context_before=context_before, context_after=context_after,
            block_lines=block_lines, alias_used=alias_used, form6_conversion=form6_conversion,
            unbounded=unbounded)

    def _resolve_line_range_edit(
        self, all_lines: list[str], *, start_line: int, end_line: int, start_text: str,
        end_text: str, new_text: str, context_before: str | None, context_after: str | None,
        subject: str, reread_hint: str, block_lines: list[str] | None = None,
        unbounded: bool = False,
    ) -> LineRangeEdit:
        """Locate the inclusive line range `[start_line, end_line]` within `all_lines` and
        return `all_lines` with that range replaced by `new_text`'s lines.

        When the caller supplied a real line hint (`not unbounded`) and the anchor already
        matches exactly there, with no `context_before`/`context_after` also supplied, this
        applies the edit at that exact hint immediately and returns -- no scan for other nearby
        candidates at all, even if the same content also occurs elsewhere within the drift
        radius. A caller who names a specific, correct location doesn't need it cross-checked
        against lookalikes; the scan below exists to *find* the right location when the hint
        alone doesn't already pin it down (drift correction: the anchor doesn't match at the
        hint) or doesn't exist at all (`unbounded` search), or when the caller explicitly asks
        for the extra check via `context_before`/`context_after`.

        `block_lines`, when given (an `old_text` call's full split lines), is verified against
        the *entire* candidate span rather than just its first/last line — a strict superset of
        the `start_text`/`end_text` endpoints-only check, so a genuine wrong-location match is
        less likely, never more.

        `unbounded` (only ever True alongside `block_lines`, for an `old_text` call with no line
        hint at all) makes the drift search scan the entire subject instead of a window within
        `self._drift_search_radius` lines of `start_line`/`end_line` -- those two are still a
        concrete `1`/`len(block_lines)` seed (see `_NormalizedEditArgs.unbounded`), just not a
        real hint, so error messages describe the search as spanning the whole subject rather
        than naming that seed as if the caller had supplied it.

        Raises `ValueError` for an out-of-range span, an anchor mismatch with no nearby drift
        candidate, or an ambiguous match among more than one drift candidate.
        """
        total_lines = len(all_lines)

        if total_lines == 0:
            if not (start_line == 1 and end_line == 0):
                raise ValueError(
                    f"{subject} is empty; the only valid edit is "
                    "`{ \"start_line\": 1, \"end_line\": 0, \"start_text\": \"\", "
                    "\"end_text\": \"\": new_text: \"<new-content>\" }` to insert content")
            if start_text != "" or end_text != "":
                raise ValueError(
                    f"{subject} is empty; start_text and end_text must both be \"\", "
                    f"got start_text={start_text!r}, end_text={end_text!r}")
            return LineRangeEdit(
                resolved_start_line=start_line, resolved_end_line=end_line, line_hint_matched=True,
                new_lines=new_text.splitlines())

        if start_line < 1 or end_line < start_line or end_line > total_lines:
            if unbounded:
                raise ValueError(
                    f"old_text has {len(block_lines or [])} line(s), longer than the "
                    f"{total_lines}-line subject")
            raise ValueError(
                f"start_line={start_line}, end_line={end_line} out of range for a "
                f"{total_lines}-line subject (expected 1 <= start_line <= end_line <= {total_lines})")

        # Fast path: a caller-supplied line hint (not `unbounded` -- old_text's "no hint at
        # all" search mode -- and not a bare drift-correction fallback) whose anchor already
        # matches exactly at that hint is accepted immediately, with no scan for other nearby
        # candidates at all. The caller pointed at a specific location and it's correct there,
        # so there's nothing to disambiguate -- unlike the drift-correction case (the anchor
        # does NOT match at the hint), where a scan is the only way to find the right location
        # at all. Skipped when context_before/context_after are also supplied: an explicit
        # context constraint signals the caller wants it verified against other nearby
        # candidates too, so that case still goes through the full scan below.
        if not unbounded and context_before is None and context_after is None:
            if block_lines is not None:
                hinted_matches = (
                    all_lines[start_line - 1:start_line - 1 + len(block_lines)] == block_lines)
            else:
                hinted_matches = (
                    all_lines[start_line - 1] == start_text and all_lines[end_line - 1] == end_text)
            if hinted_matches:
                new_lines = (
                    all_lines[:start_line - 1] + new_text.splitlines() + all_lines[end_line:])
                return LineRangeEdit(
                    resolved_start_line=start_line, resolved_end_line=end_line,
                    line_hint_matched=True, new_lines=new_lines)

        candidates = self._find_drift_candidates(
            all_lines, start_line=start_line, end_line=end_line, start_text=start_text,
            end_text=end_text, context_before=context_before, context_after=context_after,
            block_lines=block_lines, unbounded=unbounded)

        fuzzy = False
        whitespace_message: str | None = None
        if len(candidates) == 1:
            resolved_start_line = candidates[0]
            resolved_end_line = resolved_start_line + (end_line - start_line)
            line_hint_matched = (not unbounded) and resolved_start_line == start_line
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
                fuzzy=True, block_lines=block_lines, unbounded=unbounded)
            if len(fuzzy_candidates) == 1:
                fuzzy = True
                resolved_start_line = fuzzy_candidates[0]
                resolved_end_line = resolved_start_line + (end_line - start_line)
                line_hint_matched = (not unbounded) and resolved_start_line == start_line
                if block_lines is not None:
                    whitespace_message = (
                        "old_text was matched by ignoring leading/trailing whitespace on one "
                        "or more of its lines; for most accurate matching, be precise with "
                        "leading and trailing whitespace.")
                else:
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
            elif block_lines is not None:
                if unbounded:
                    if context_before is None and context_after is None:
                        raise ValueError(
                            f"old_text does not match any location in the {total_lines}-line "
                            f"file; {reread_hint}")
                    raise ValueError(
                        f"old_text matches no location in the {total_lines}-line file that "
                        "also satisfies the supplied context_before/context_after; omit or "
                        f"correct context_before/context_after, or {reread_hint} to confirm the "
                        "surrounding lines")
                block_len = len(block_lines)
                hinted_actual = all_lines[start_line - 1:start_line - 1 + block_len]
                if hinted_actual != block_lines:
                    mismatch_index = next(
                        i for i, (actual_line, expected_line)
                        in enumerate(zip(hinted_actual, block_lines)) if actual_line != expected_line)
                    raise ValueError(
                        f"old_text does not match the file's content at lines "
                        f"{start_line}..{end_line} (line #{start_line + mismatch_index} "
                        f"differs): {hinted_actual[mismatch_index]!r} != "
                        f"{block_lines[mismatch_index]!r}, and no matching location was found "
                        f"within {self._drift_search_radius} lines; {reread_hint}")
                raise ValueError(
                    f"old_text matches lines {start_line}..{end_line}, but the supplied "
                    "context_before/context_after does not match the surrounding lines there "
                    f"(and no other nearby location within {self._drift_search_radius} lines "
                    "satisfies every constraint); omit or correct context_before/context_after, "
                    f"or {reread_hint} to confirm the surrounding lines")
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
            descriptions = "\n * ".join(
                self._describe_candidate_neighbors(all_lines, candidate, span, preview_lines)
                for candidate in candidates)
            anchor_desc = "old_text" if block_lines is not None else "start_text/end_text"
            location_clause = (
                f"matches at {len(candidates)} locations in the file" if unbounded else
                f"for start_line={start_line}, end_line={end_line} matches at {len(candidates)} "
                f"locations within {self._drift_search_radius} lines")
            raise ValueError(
                f"Ambiguous match: {anchor_desc} {location_clause} "
                f"(lines {candidates}):\n * {descriptions}\n"
                "Retry with one of the exact json fragments shown above to choose a target. "
                "If a context_before or context_after value is multiple lines, keep them all. "
                "For a candidate at the file's start/end, use "
                "context_before_start=true / context_after_end=true exactly as shown above "
                "rather than trying to send an empty string; omitting context_before/"
                "context_after entirely means \"don't check that side\", which is different "
                "from asserting there's genuinely nothing there.")

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
        and recommends `context_before_start=true`/`context_after_end=true` (see
        `_normalize_edit_args`) rather than an empty-string `context_before`/`context_after` --
        models reliably mis-send the latter as bare, unquoted whitespace in the tool-call JSON,
        producing a parse error instead of the intended assertion that there's genuinely
        nothing on that side (see `_context_matches_candidate`).
        `preview_lines` comes from `_minimal_disambiguating_window`, not a fixed constant,
        since one line is not always enough (see that method's docstring)."""
        candidate_end = candidate_start + span
        before_lines = all_lines[max(0, candidate_start - 1 - preview_lines):candidate_start - 1]
        after_lines = all_lines[candidate_end:candidate_end + preview_lines]
        before_value = "\n".join(before_lines)
        after_value = "\n".join(after_lines)
        before = (
            f"context_before=\"{repr(before_value)[1:-1].replace('"', '\\"')}\"" if before_lines
            else "context_before_start=true")
        after = (
            f"context_after=\"{repr(after_value)[1:-1].replace('"', '\\"')}\"" if after_lines
            else "context_after_end=true")
        return (f"For line {candidate_start}: use "
            f"`{{ \"start_line\": {candidate_start}, {before}, {after} }}`.")

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
        fuzzy: bool = False, block_lines: list[str] | None = None, unbounded: bool = False,
    ) -> list[int]:
        """Return every start-line position within `self._drift_search_radius` of `start_line`
        (inclusive, clipped to the file's bounds) where the anchor matches and, if
        `context_before`/`context_after` are given, where those also match (see
        `_context_matches_candidate`). `start_line` itself (zero drift) is an ordinary
        candidate, not special-cased, so an undrifted hint naturally resolves as the sole
        match. Candidates are returned in ascending line-number order.

        The anchor check is `start_text` at `candidate_start` and `end_text` at
        `candidate_start + (end_line - start_line)` (preserving the caller's requested span
        length), unless `block_lines` is given (an `old_text` call's full split lines), in
        which case the *entire* candidate span must equal `block_lines` — a strict superset of
        the endpoints-only check.

        With `fuzzy=True`, every line comparison (anchor or full block) ignores leading/trailing
        whitespace on both the candidate and the file's line; the `context_before`/
        `context_after` check is still exact, since those are an optional disambiguation aid a
        caller supplies deliberately rather than an anchor likely to be captured with stray
        whitespace.

        `unbounded=True` (only ever paired with `block_lines`, for an `old_text` call with no
        line hint) scans every possible candidate start in `all_lines` instead of clipping to
        `self._drift_search_radius` lines of `start_line`.
        """
        total_lines = len(all_lines)
        span = end_line - start_line
        if unbounded:
            lo = 1
            hi = total_lines - span
        else:
            radius = self._drift_search_radius
            lo = max(1, start_line - radius)
            hi = min(total_lines - span, start_line + radius)

        candidates: list[int] = []
        for candidate_start in range(lo, hi + 1):
            candidate_end = candidate_start + span
            if block_lines is not None:
                actual = all_lines[candidate_start - 1:candidate_end]
                if not self._block_matches(actual, block_lines, fuzzy):
                    continue
            else:
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

    @staticmethod
    def _block_matches(actual: list[str], expected: list[str], fuzzy: bool) -> bool:
        """`old_text`'s full-block counterpart to `_anchor_matches`: True if every line of
        `actual` (a candidate span from the file) equals the corresponding line of `expected`
        (the caller's `old_text`, split), exactly by default or per-line whitespace-tolerant
        with `fuzzy=True`."""
        if len(actual) != len(expected):
            return False
        if not fuzzy:
            return actual == expected
        return all(a.strip() == e.strip() for a, e in zip(actual, expected))

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
