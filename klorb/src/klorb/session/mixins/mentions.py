# © Copyright 2026 Aaron Kimball
r"""@mention file inlining: when a user's prompt contains `@<filename>`, the referenced file's
contents are read via `ReadFileCore` and returned as `MessageFragment`s attached alongside the
prompt, so the model sees the file's contents without needing a separate `ReadFile` tool call.

Filenames after `@` may contain escaped spaces (`\ `), backslashes (`\\`), and double-quote
marks (`\"`); each is unescaped before the file is opened.  A backslash followed by any other
character is left as-is (the backslash is literal).  Quoted filenames (`@"path with spaces"`)
are also supported -- the quotes are stripped and the inner escapes processed identically.

See docs/specs/at-mention-file-inlining.md for the full design.
"""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from klorb.message import MessageFragment

if TYPE_CHECKING:
    from klorb.tools.util.read_file_core import ReadFileCore

logger = logging.getLogger(__name__)

_AT_MENTION_RE = re.compile(
    r'(?<!\S)@"((?:[^"\\\n]|\\[\s\S])*?)"'  # group 1: quoted filename (escapes allowed)
    r"|"
    r"(?<!\S)@((?:[^\s\"\\]|\\[\s\S])+)"     # group 2: unquoted filename (escapes allowed)
)
r"""Matches an `@mention` in a user prompt.

Group 1 captures a double-quoted filename (quotes stripped at match time); group 2 captures an
unquoted token of non-whitespace characters, where a backslash-escaped character (including
`\ `) counts as one unit.  Escapes are resolved by `unescape_mention_filename`."""


def unescape_mention_filename(raw: str) -> str:
    r"""Resolve escape sequences in a raw `@mention` filename.

    `\ ` → space, `\\` → `\`, `\"` → `"`.  A backslash followed by any other character
    is kept as-is (both the backslash and the following character are literal).
    """
    out: list[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            if nxt == " ":
                out.append(" ")
            elif nxt == "\\":
                out.append("\\")
            elif nxt == '"':
                out.append('"')
            else:
                out.append(ch)
                out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    result = "".join(out)
    if result != raw:
        logger.debug("Unescaped @mention filename %r -> %r", raw, result)
    return result


def has_at_mention(prompt: str) -> bool:
    """Return `True` if *prompt* contains any `@mention`."""
    return bool(_AT_MENTION_RE.search(prompt))


def _format_attachment(
    ordinal: int,
    filename: str,
    read_result: dict[str, Any],
) -> str:
    """Build the `AttachedFile` block text for one `@mention`ed file."""
    total_lines: int = read_result["total_lines"]
    truncated: bool = read_result["truncated"]
    content: str = read_result["content"]
    header = (
        f"Filename: {filename}\n"
        f"Attachment Id: {ordinal}\n"
        f"Total lines: {total_lines}\n"
        f"Truncated: {'true' if truncated else 'false'}\n"
    )

    is_error: bool = bool(read_result.get("error", False))
    if is_error:
        header = header + "Error: True\n"

    return f"{header}\n{content}"


def resolve_at_mentions(
    prompt: str,
    read_file_core: "ReadFileCore",
    workspace_path: Path,
) -> list[MessageFragment] | None:
    """Build one `MessageFragment` per unique file `@mention`ed in *prompt*, in first-seen
    order, or `None` if *prompt* contains no mentions.

    Each fragment wraps an `AttachedFile` block (`_format_attachment`) with the file's
    contents in `ReadFileCore`-formatted line-number style. Mentions that fail to read (file
    not found, permission denied, etc.) get a fragment with an error note instead of content.
    *prompt* itself is never modified -- the caller (`Session.send_turn`) is responsible for
    attaching its own fragment for the prompt text alongside whatever this function returns.

    Duplicate mentions of the same filename are resolved only once (one read, one fragment,
    same ordinal reused for every occurrence).
    """
    mentions = list(_AT_MENTION_RE.finditer(prompt))
    if not mentions:
        logger.debug("No @mentions found in prompt (%d chars)", len(prompt))
        return None

    logger.debug("Found %d @mention occurrence(s) in prompt", len(mentions))
    seen_filenames: dict[str, int] = {}  # filename -> ordinal
    fragments: list[MessageFragment] = []
    for match in mentions:
        raw = match.group(1) if match.group(1) is not None else match.group(2)
        filename = unescape_mention_filename(raw)
        if filename in seen_filenames:
            logger.debug(
                "@mention %r already resolved as attachment id=%d; skipping duplicate read",
                filename, seen_filenames[filename],
            )
            continue
        ordinal = len(seen_filenames) + 1
        seen_filenames[filename] = ordinal
        logger.debug("Resolving @mention %r as attachment id=%d", filename, ordinal)
        resolved = _resolve_and_read(filename, read_file_core, workspace_path)
        fragments.append(
            MessageFragment(type="text", text=_format_attachment(ordinal, filename, resolved)))

    logger.info(
        "Resolved %d @mention fragment(s) from prompt: %s",
        len(fragments), list(seen_filenames),
    )
    return fragments


def _resolve_and_read(
    filename: str,
    read_file_core: "ReadFileCore",
    workspace_path: Path,
) -> dict[str, Any]:
    """Resolve *filename* to an absolute path within *workspace_path* and read it.

    Returns a `ReadFileCore.apply`-shaped dict on success, or a synthetic error dict on failure.
    No permission check is performed -- the user implicitly authorized the read by @mentioning the
    file.
    """
    path = Path(filename)
    if not path.is_absolute():
        path = workspace_path / path
    path = path.resolve()
    logger.debug("Reading @mention %r resolved to path %s", filename, path)

    try:
        result = read_file_core.apply(path, {})
    except (OSError, ValueError) as exc:
        logger.debug("@mention read failed for %s: %s", filename, exc)
        return {
            "start_line": 0,
            "end_line": 0,
            "total_lines": 0,
            "truncated": False,
            "error": True,
            "content": f"(error reading file: {exc})",
        }
    logger.debug(
        "@mention read %s: lines %d-%d of %d (truncated=%s)",
        filename, result["start_line"], result["end_line"],
        result["total_lines"], result["truncated"],
    )
    return result
