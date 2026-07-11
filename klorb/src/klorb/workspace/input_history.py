# © Copyright 2026 Aaron Kimball
"""File-backed persistence for the prompt-input history (the up/down-arrow recall list),
keyed per project under `$KLORB_DATA_DIR/projects/<id-or-hash>-<basename>/history`.

Each registered project (`klorb.workspace.Workspace.id` from `projects.json`) gets one
per-project directory; an unregistered workspace (one the user declined to open as a
project, or one klorb was launched into before any bootstrap) falls back to a stable hash
of its canonical path so two klorb instances opened in the *same* folder still share one
history file even without a `projects.json` entry.

The `history` file is newline-delimited: one previously-submitted prompt per line. Because
prompts can themselves contain newlines (Ctrl+Enter inserts a literal newline), each entry
is escaped before it's written (`\\` -> `\\\\`, `\n` -> `\\n`, `\r` -> `\\r`) and unescaped
on the way back into the input box, so a recalled multi-line prompt round-trips verbatim.

Writes are strictly append-only: every submitted message opens the file in append mode,
writes its escaped entry plus a trailing `\n`, and closes it. No caller ever rewrites the
whole file, so two (or more) klorb instances editing in the same folder concurrently each
just append their own most-recent message and never clobber one another's history — each
process only ever knows its own in-memory view; the file is the shared, append-only log.
"""

import hashlib
import logging
import os
from pathlib import Path

from klorb.paths import KLORB_DATA_DIR
from klorb.workspace import Workspace

logger = logging.getLogger(__name__)

PROJECTS_DIR_NAME = "projects"
"""Subdirectory of `$KLORB_DATA_DIR` holding one per-project directory each."""

HISTORY_FILENAME = "history"
"""Name of the newline-delimited prompt-history file within a per-project directory."""


def _project_token(workspace: Workspace) -> str:
    """The unique-per-project token used in its per-project directory's name: the
    registered project's uuid (`workspace.id`) when it has one, otherwise a stable
    12-hex-char hash of the canonical workspace path so two instances opened in the same
    unregistered folder still converge on the same history file."""
    if workspace.id is not None:
        return workspace.id
    canonical = str(workspace.path.resolve(strict=False))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:12]


def project_history_dir(workspace: Workspace) -> Path:
    """The per-project directory holding (at least) that project's `history` file:
    `$KLORB_DATA_DIR/projects/<token>-<basename>`, where `<token>` is the project uuid (or a
    stable path-hash for an unregistered workspace) and `<basename>` is the last path
    element of `workspace.path` — e.g. a workspace at `/home/aaron/src/foobar` registered
    with uuid `abcd-1234` maps to `…/projects/abcd-1234-foobar`."""
    basename = workspace.path.name or "workspace"
    return KLORB_DATA_DIR / PROJECTS_DIR_NAME / f"{_project_token(workspace)}-{basename}"


def project_history_path(workspace: Workspace) -> Path:
    """The `history` file path for `workspace` (see `project_history_dir`)."""
    return project_history_dir(workspace) / HISTORY_FILENAME


def _escape_entry(entry: str) -> str:
    """Escape a prompt so it occupies exactly one line in the history file: backslash first
    (so a literal `\\n` in a prompt isn't mistaken for an escaped newline on read), then the
    CR/LF characters. Reversed verbatim by `_unescape_entry`."""
    return (
        entry.replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _unescape_entry(line: str) -> str:
    """Reverse `_escape_entry`: turn the escaped single-line form back into the original
    multi-line prompt. Processes the string left-to-right so a `\\\\n` sequence (an escaped
    backslash followed by a literal `n`) round-trips as `\\n` rather than a newline."""
    result: list[str] = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n:
            nxt = line[i + 1]
            if nxt == "n":
                result.append("\n")
            elif nxt == "r":
                result.append("\r")
            elif nxt == "\\":
                result.append("\\")
            else:
                # An unrecognized escape: keep both characters verbatim rather than
                # silently dropping the backslash, so a hand-edited file doesn't lose data.
                result.append(ch)
                result.append(nxt)
            i += 2
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def load_history(path: Path) -> list[str]:
    """Read the on-disk history at `path` back into the in-memory recall list, newest entry
    last (the order it was appended). A missing file (no prompts submitted in this project
    yet) yields `[]`. A trailing newline — always present for files written via
    `append_history` — is treated as the separator after the last entry, not an empty
    final entry, so the list never grows a spurious blank tail."""
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    # Split on the record separator; a trailing newline leaves one empty trailing element
    # that isn't a real entry.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [_unescape_entry(line) for line in lines]


def append_history(path: Path, entry: str) -> None:
    """Append `entry` to the on-disk history at `path`, escaped to a single line (see
    `_escape_entry`) and terminated with a `\n`. Creates `path`'s parent directory if it
    doesn't exist yet. Strictly append-only — the file is opened in `"a"` mode and never
    rewritten, so concurrent klorb instances in the same folder each just append their own
    most-recent message without clobbering one another's history."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _escape_entry(entry) + "\n"
    # Open in append mode with encoding so writes on different platforms stay text-stable;
    # the leading `.` prefix on the tmp name is just a cosmetic marker for any tmpfile a
    # filesystem creates internally. We append directly (not via atomic replace) because
    # atomic replace would rewrite the whole file — the opposite of what this store wants.
    with open(path, "a", encoding="utf-8") as history_file:
        history_file.write(line)
        history_file.flush()
        os.fsync(history_file.fileno())
