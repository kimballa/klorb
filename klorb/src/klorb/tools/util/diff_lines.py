# © Copyright 2026 Aaron Kimball
"""Structured, UI-agnostic diff computation shared by `EditFileCore`/`CreateFileCore`: turns an
old/new pair of line lists into `DiffHunk`s a UI can render with a line-number gutter and
add/delete coloring, without either side depending on the other (`klorb.tools` has no dependency
on `klorb.tui`, or on any rendering library) -- see docs/specs/terminal-repl.md's tool-call
preview section for how a `Tool`'s `diff_preview()` turns these back into something renderable.
"""

import difflib
from typing import Literal

from pydantic import BaseModel

DIFF_CONTEXT_LINES = 8
"""How many lines of unchanged context `build_diff_hunks` keeps on either side of a change --
also the distance within which two changes are merged into a single hunk rather than kept as
two separate ones (see `difflib.SequenceMatcher.get_grouped_opcodes`'s own `n` parameter, which
this is passed into directly). The full amount only ever shows in a diff's expanded/click-to-
overlay view -- the compact inline history preview starts much closer to the change itself; see
`klorb.tui.formatting.render_diff_content`."""


class DiffLine(BaseModel):
    """One rendered line of a diff: `kind` is `"context"` (unchanged, present on both sides),
    `"add"` (present only in the new content, so `old_lineno` is `None`), or `"del"` (present
    only in the old content, so `new_lineno` is `None`). `old_lineno`/`new_lineno` are 1-indexed,
    matching the line numbers `EditFileCore`/`ReadFileCore` already show elsewhere."""

    kind: Literal["context", "add", "del"]
    old_lineno: int | None
    new_lineno: int | None
    text: str


class DiffHunk(BaseModel):
    """A contiguous run of `DiffLine`s -- one or more changes plus up to `DIFF_CONTEXT_LINES` of
    surrounding unchanged lines on each side. Two changes closer together than
    `2 * DIFF_CONTEXT_LINES` lines apart are merged into one hunk rather than kept separate, same
    as a standard unified diff."""

    lines: list[DiffLine]


def build_diff_hunks(
    old_lines: list[str], new_lines: list[str], context: int = DIFF_CONTEXT_LINES,
) -> list[DiffHunk]:
    """Diff `old_lines` against `new_lines` and group the result into `DiffHunk`s, each carrying
    up to `context` lines of unchanged context on either side of a change (nearby changes merged
    into one hunk when their context windows would overlap) -- delegates the grouping itself to
    `difflib.SequenceMatcher.get_grouped_opcodes`, which already implements exactly this
    hunk-with-context-and-merging behavior.

    `old_lines=[]` (a brand-new file/memory/scratchpad) yields a single hunk of nothing but
    `"add"` lines, with no context to show. `new_lines=[]` (the whole subject deleted) is the
    mirror image, all `"del"` lines.
    """
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    hunks: list[DiffHunk] = []
    for group in matcher.get_grouped_opcodes(n=context):
        lines: list[DiffLine] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for offset in range(i2 - i1):
                    lines.append(DiffLine(
                        kind="context", old_lineno=i1 + offset + 1, new_lineno=j1 + offset + 1,
                        text=old_lines[i1 + offset]))
                continue
            if tag in ("replace", "delete"):
                for old_index in range(i1, i2):
                    lines.append(DiffLine(
                        kind="del", old_lineno=old_index + 1, new_lineno=None,
                        text=old_lines[old_index]))
            if tag in ("replace", "insert"):
                for new_index in range(j1, j2):
                    lines.append(DiffLine(
                        kind="add", old_lineno=None, new_lineno=new_index + 1,
                        text=new_lines[new_index]))
        hunks.append(DiffHunk(lines=lines))
    return hunks
