# © Copyright 2026 Aaron Kimball
"""Line-range read mechanic for the file and scratchpad tool pairs."""

from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable

from klorb.tools.util.response_headers import format_header_lines
from klorb.tools.util.secret_redaction import SecretRedactor

if TYPE_CHECKING:
    from klorb.session import Session

READ_PREVIEW_MAX_LINES = 4
"""How many numbered lines a `Tool.read_preview()` override keeps in `ReadPreview.preview_lines`
for the inline compact preview."""

_READ_RESULT_HEADER_ORDER = (
    "namespace", "filename", "name", "path",
    "start_line", "end_line", "total_lines", "truncated", "truncation_cause", "next_start_line",
)
"""Key order `format_read_result()` renders a `ReadFileCore.apply()`-shaped result dict's header
lines in."""


def format_read_result(result: dict[str, Any]) -> str:
    """Render a `ReadFileCore.apply()`-shaped result dict as `key: value` header lines in
    `_READ_RESULT_HEADER_ORDER`, followed by a blank line and `result["content"]`."""
    header_lines = format_header_lines(
        result, _READ_RESULT_HEADER_ORDER, known_elsewhere=frozenset({"content"}))
    content: str = result.get("content", "")
    return "\n".join(header_lines) + "\n\n" + content


@dataclass
class FullFileView:
    """The whole subject a `Tool`'s `read_preview()` shows in its click-to-expand overlay.

    `lines` is `None` when `error` is set; `scroll_to_line` is the 1-indexed line the overlay
    should position at the top of its viewport, matching the range originally read.
    """

    lines: list[tuple[int, str]] | None
    error: str | None
    scroll_to_line: int


def parse_numbered_content(content: str) -> list[tuple[int, str]]:
    """Parse `ReadFileCore.apply()`'s `content` field (lines of the form `"N|text"`, one per
    line) back into `(line_number, text)` pairs. Splits on the first `"|"` only, since `text`
    itself may legitimately contain `"|"` characters. An empty `content` (a zero-line read)
    yields an empty list rather than one spurious empty pair.
    """
    if not content:
        return []
    pairs: list[tuple[int, str]] = []
    for line in content.split("\n"):
        lineno_text, _, text = line.partition("|")
        pairs.append((int(lineno_text), text))
    return pairs


def read_full_file_lines(open_resource: Callable[[], IO[str]], scroll_to_line: int) -> FullFileView:
    """Read every line out of `open_resource()` (a zero-argument callable returning an open text
    handle), numbering them from 1, for a `Tool`'s `read_preview()` click-to-expand overlay.
    Catches `OSError` (a deleted/moved/unreadable subject) and returns a `FullFileView` carrying
    `error` instead of raising.
    """
    try:
        with open_resource() as file:
            lines = file.read().splitlines()
    except OSError as exc:
        return FullFileView(lines=None, error=str(exc), scroll_to_line=scroll_to_line)
    return FullFileView(
        lines=list(enumerate(lines, start=1)), error=None, scroll_to_line=scroll_to_line)


class ReadFileCore:
    """Reads up to `max_lines` lines from `path`, prefixed with 1-indexed line numbers.

    Lines longer than `max_line_length` are wrapped (not truncated) at that character width,
    with each wrapped segment counting toward the `max_lines` page-size limit. Wrapped
    continuation lines repeat the same line number prefix so `parse_numbered_content()` can
    round-trip the output.

    Reads go through `open_resource()`, which handles both a real filesystem `Path` and any other
    `importlib.resources` `Traversable` (a packaged resource that may have no filesystem path at
    all). A subclass may override `open_resource()` to obtain the handle differently."""

    def __init__(self, max_lines: int, max_line_length: int) -> None:
        self._max_lines = max_lines
        self._max_line_length = max_line_length

    @property
    def max_lines(self) -> int:
        """Return the per-call line cap this core was constructed with."""
        return self._max_lines

    @property
    def max_line_length(self) -> int:
        """Return the per-line character cap this core was constructed with."""
        return self._max_line_length

    def parameter_properties(self) -> dict[str, Any]:
        """Return the `start_line`/`end_line` JSON-schema properties for the read tools' `parameters()`."""
        return {
            "start_line": {
                "type": "integer",
                "description": (
                    "Line number to start reading from. Omitted starts at the beginning."
                ),
            },
            "end_line": {
                "type": "integer",
                "description": (
                    f"Line to stop reading at. Omitted reads {self._max_lines} lines."
                ),
            },
        }

    def open_resource(self, path: Traversable) -> IO[str]:
        """Open `path` for text reading and return the file handle `apply()` reads from. A real
        filesystem `Path` is opened with the builtin `open()`; any other `Traversable` (a packaged
        resource with no filesystem path) is opened via its own `open()` through the
        `importlib.resources` loader. A subclass may override to obtain the handle differently."""
        if isinstance(path, Path):
            return open(path, encoding="utf-8")
        return path.open("r", encoding="utf-8")

    def apply(
        self, path: Traversable, args: dict[str, Any], *,
        redactor: SecretRedactor | None = None, session: "Session | None" = None,
    ) -> dict[str, Any]:
        """Read `path` per `args`' `start_line`/`end_line`, returning `start_line`, `end_line`,
        `total_lines`, `truncated`, `content` (the caller adds `filename`/`name` if it has one),
        and, when `truncated` is true, `next_start_line`. `args`' `start_line`/`end_line` are
        validated (raising `ValueError`) before `path` is opened via `open_resource()`.

        Lines longer than `max_line_length` are wrapped at that width, with each wrapped segment
        counting toward the `max_lines` page-size limit. An unlisted `wrap_width` arg in `args`
        overrides the default wrap width when set to a positive number.

        When `redactor` is given, each raw line is passed through `redactor.redact(session,
        line)` before wrapping, masking likely credentials out of `content`. See
        docs/specs/secret-redaction.md. `total_lines`/`truncated`/`next_start_line` are computed
        from the real (unredacted) line count either way, so paging stays accurate.
        """
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        if start_line is not None and start_line < 0:
            raise ValueError(
                f"start_line must be >= 0, got {start_line}; there is no negative/"
                "relative-to-the-end addressing (e.g. -1 does not mean 'last line') — call "
                "with no start_line/end_line to see total_lines and find the last line's number")
        if end_line is not None and end_line < 1:
            raise ValueError(f"end_line must be >= 1, got {end_line}")

        # A start_line of 0 (or omitted) means "start at the beginning."
        effective_start = start_line if start_line else 1
        if end_line is not None and end_line < effective_start:
            raise ValueError(
                f"end_line ({end_line}) must be >= start_line ({effective_start})")

        with self.open_resource(path) as file:
            all_lines = file.read().splitlines()
        total_lines = len(all_lines)

        # An unlisted wrap_width arg overrides the default max_line_length.
        wrap_width = self._max_line_length
        raw_wrap_width = args.get("wrap_width")
        if raw_wrap_width is not None:
            try:
                parsed = int(raw_wrap_width)
            except (ValueError, TypeError):
                parsed = 0
            if parsed > 0:
                wrap_width = parsed

        requested_end = end_line if end_line is not None else effective_start + self._max_lines - 1
        capped_end = min(requested_end, effective_start + self._max_lines - 1, total_lines)

        # Select the raw lines for the requested range.  We may not use all of them if
        # wrapping pushes us past the max_lines budget.
        if capped_end >= effective_start:
            selected_lines = all_lines[effective_start - 1:capped_end]
        else:
            selected_lines = []

        # Mask likely credentials before wrapping, so a redacted secret can't be split across
        # two wrapped segments.
        if redactor is not None:
            selected_lines = [redactor.redact(session, line) for line in selected_lines]

        # Build output lines, wrapping long lines and counting toward max_lines.
        output_segments: list[str] = []
        lines_emitted = 0
        truncated = False
        truncation_cause: str | None = None
        last_line_number = effective_start - 1
        first_line_of_read = True

        for i, line in enumerate(selected_lines):
            lineno = effective_start + i
            wrapped = _wrap_line(line, lineno, wrap_width)
            remaining_budget = self._max_lines - lines_emitted

            if len(wrapped) <= remaining_budget:
                output_segments.extend(wrapped)
                lines_emitted += len(wrapped)
                last_line_number = lineno
                first_line_of_read = False
            else:
                # This line's wrapped segments don't all fit in the remaining budget.
                output_segments.extend(wrapped[:remaining_budget])
                lines_emitted += remaining_budget
                truncated = True
                last_line_number = lineno
                if first_line_of_read:
                    # The first requested line alone exceeds the entire budget at this
                    # wrap_width. A ridiculously long line.  Suggest doubling wrap_width
                    # so the caller can retry with a wider wrap, or skip to the next line.
                    truncation_cause = (
                        f"The response ended in the middle of the first requested line. "
                        f"Resume with start_line={lineno}, wrap_width={wrap_width * 2} to "
                        "re-read starting from that line to read the whole line, or use "
                        f"start_line={lineno + 1} to just advance to the next line.")
                else:
                    truncation_cause = (
                        f"The response ended mid-line. Resume with start_line={lineno} to "
                        "re-read starting from that line to read the whole line.")
                break

        if not truncated:
            truncated = last_line_number < total_lines

        content = "\n".join(output_segments)
        returned_end = last_line_number

        result: dict[str, Any] = {
            "start_line": effective_start,
            "end_line": returned_end,
            "total_lines": total_lines,
            "truncated": truncated,
            "content": content,
        }
        if truncated:
            if truncation_cause is None:
                result["next_start_line"] = returned_end + 1
            else:
                result["next_start_line"] = returned_end
                result["truncation_cause"] = truncation_cause
        return result


def _wrap_line(line: str, lineno: int, wrap_width: int) -> list[str]:
    """Split `line` into segments of at most `wrap_width` characters, each prefixed with
    `"{lineno}|"`.  A line that fits within `wrap_width` is returned as a single-element list."""
    prefix = f"{lineno}|"
    if len(line) <= wrap_width:
        return [f"{prefix}{line}"]
    segments: list[str] = []
    offset = 0
    while offset < len(line):
        segments.append(f"{prefix}{line[offset:offset + wrap_width]}")
        offset += wrap_width
    return segments
