# © Copyright 2026 Aaron Kimball
r"""@mention file inlining: when a user's prompt contains `@<filename>`, the referenced file's
contents are attached alongside the prompt as one or more `MessageFragment`s, so the model sees
the file's contents without needing a separate `ReadFile` tool call. A mention whose bytes are
recognized as an image (see `detect_mention_mime_type`) is instead resized/transcoded via
`klorb.images.prepare.prepare_image_for_model` and attached as an `image_url` fragment, the same
pipeline a drag-drop/paste attachment goes through (see docs/specs/vision-image-input.md); every
other mention is read via `ReadFileCore` and attached as text, as before.

Filenames after `@` may contain escaped spaces (`\ `), backslashes (`\\`), and double-quote
marks (`\"`); each is unescaped before the file is opened.  A backslash followed by any other
character is left as-is (the backslash is literal).  Quoted filenames (`@"path with spaces"`)
are also supported -- the quotes are stripped and the inner escapes processed identically.

See docs/specs/at-mention-file-inlining.md for the full design.
"""

import logging
import mimetypes
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from klorb.images.prepare import (
    ImagePipelineConfig,
    ImageTooLargeError,
    PreparedImage,
    prepare_image_for_model,
)
from klorb.message import MessageFragment
from klorb.models.model import Model

if TYPE_CHECKING:
    from klorb.tools.util.read_file_core import ReadFileCore

logger = logging.getLogger(__name__)

_MENTION_MIME_SNIFF_BYTES = 16
"""How many leading bytes `_resolve_mention_fragment` reads to sniff an @mentioned file's magic
signature -- enough to cover every signature in `_IMAGE_MAGIC_SIGNATURES` plus the WEBP
`RIFF....WEBP` container check."""

_IMAGE_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)
"""Leading-byte signatures `_sniff_image_mime_type` matches against an @mentioned file's head --
the formats every packaged vision model's `vision_details.supported_mime_types` overlaps on
(see docs/specs/vision-image-input.md), plus BMP. WEBP isn't a fixed prefix (its signature spans
a 4-byte size field at offset 4-7) so it's checked separately."""

_RECOGNIZED_IMAGE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"})
"""Every MIME type `detect_mention_mime_type` will report -- the extension-guessed counterpart
of `_IMAGE_MAGIC_SIGNATURES`, so a `mimetypes.guess_type()` hit for a format this module can't
magic-sniff (e.g. `image/heic`) is never treated as a recognized image mention."""


def _sniff_image_mime_type(head: bytes) -> str | None:
    """Match *head* (an @mentioned file's leading bytes) against `_IMAGE_MAGIC_SIGNATURES` plus
    the WEBP `RIFF....WEBP` container signature. Returns `None` if nothing matches."""
    for signature, mime_type in _IMAGE_MAGIC_SIGNATURES:
        if head.startswith(signature):
            return mime_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def detect_mention_mime_type(filename: str, head: bytes) -> str | None:
    """Return the image MIME type an @mentioned file's *head* bytes (and *filename*) identify
    it as, or `None` if it isn't a recognized image. Magic bytes take precedence over the file
    extension, since content is the ground truth; the extension is only consulted when the
    magic-byte sniff finds nothing (e.g. a mismatched or unusual extension for a real image
    format `_sniff_image_mime_type` doesn't cover)."""
    sniffed = _sniff_image_mime_type(head)
    if sniffed is not None:
        return sniffed
    guessed, _ = mimetypes.guess_type(filename)
    return guessed if guessed in _RECOGNIZED_IMAGE_MIME_TYPES else None


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


def _mention_read_error_result(message: object) -> dict[str, Any]:
    """Build the synthetic `_format_attachment`-shaped result for an @mention that couldn't be
    read/attached -- shared by a text-read failure and an image-mention failure (no vision
    support, unreadable bytes, or an oversized prepared image) alike."""
    return {
        "start_line": 0,
        "end_line": 0,
        "total_lines": 0,
        "truncated": False,
        "error": True,
        "content": f"(error reading file: {message})",
    }


def resolve_at_mentions(
    prompt: str,
    read_file_core: "ReadFileCore",
    workspace_path: Path,
    active_model: Model | None = None,
    image_pipeline_config: ImagePipelineConfig | None = None,
) -> list[MessageFragment] | None:
    """Build the `MessageFragment`s for every unique file `@mention`ed in *prompt*, in
    first-seen order, or `None` if *prompt* contains no mentions.

    Each mention resolves to either an `image_url` fragment (preceded by its own small text
    header) if its bytes are recognized as an image (see `detect_mention_mime_type`) and
    *active_model*/*image_pipeline_config* are both given and *active_model* supports vision, or
    a single text fragment wrapping an `AttachedFile` block (`_format_attachment`) with the
    file's contents in `ReadFileCore`-formatted line-number style otherwise -- see
    `_resolve_mention_fragments`. A mention that fails to resolve (file not found, permission
    denied, unreadable/oversized image, or an image mention with no vision-capable model) gets a
    text fragment with an error note instead of content. *prompt* itself is never modified -- the
    caller (`Session.send_turn`) is responsible for attaching its own fragment for the prompt
    text alongside whatever this function returns.

    Duplicate mentions of the same filename are resolved only once (one read, same ordinal
    reused for every occurrence).
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
        fragments.extend(_resolve_mention_fragments(
            ordinal, filename, read_file_core, workspace_path, active_model, image_pipeline_config))

    logger.info(
        "Resolved %d @mention fragment(s) from prompt: %s",
        len(fragments), list(seen_filenames),
    )
    return fragments


def _resolve_mention_path(filename: str, workspace_path: Path) -> Path:
    """Resolve *filename* to an absolute path within *workspace_path* -- shared by the text and
    image mention-resolution paths. No permission check is performed -- the user implicitly
    authorized the read by @mentioning the file."""
    path = Path(filename)
    if not path.is_absolute():
        path = workspace_path / path
    return path.resolve()


def _resolve_mention_fragments(
    ordinal: int,
    filename: str,
    read_file_core: "ReadFileCore",
    workspace_path: Path,
    active_model: Model | None,
    image_pipeline_config: ImagePipelineConfig | None,
) -> list[MessageFragment]:
    """Resolve one @mentioned *filename* into its `MessageFragment`(s): an image header text
    fragment plus an `image_url` fragment (see `_resolve_mention_image`) if the file's leading
    bytes identify it as an image (`detect_mention_mime_type`), or a single text fragment
    wrapping a `ReadFileCore`-formatted read (`_resolve_and_read`) otherwise."""
    path = _resolve_mention_path(filename, workspace_path)
    try:
        with open(path, "rb") as fh:
            head = fh.read(_MENTION_MIME_SNIFF_BYTES)
    except OSError as exc:
        logger.debug("@mention read failed for %s: %s", filename, exc)
        return [MessageFragment(
            type="text", text=_format_attachment(ordinal, filename, _mention_read_error_result(exc)))]

    if detect_mention_mime_type(filename, head) is not None:
        return _resolve_mention_image(ordinal, filename, path, active_model, image_pipeline_config)

    resolved = _resolve_and_read(filename, read_file_core, workspace_path)
    return [MessageFragment(type="text", text=_format_attachment(ordinal, filename, resolved))]


def _image_attachment_header_text(ordinal: int, filename: str, prepared: PreparedImage) -> str:
    """Text-fragment header `_resolve_mention_image` places immediately ahead of an @mentioned
    image's `image_url` fragment -- mirrors `_format_attachment`'s `Filename`/`Attachment Id`
    framing so the model can correlate an image mention with a text one by ordinal, per vendor
    guidance that an image should follow descriptive text (see docs/specs/vision-image-input.md's
    `_image_header_text`)."""
    return (
        f"Filename: {filename}\n"
        f"Attachment Id: {ordinal}\n"
        f"Type: image ({prepared.mime_type}, {prepared.width}x{prepared.height})\n"
    )


def _resolve_mention_image(
    ordinal: int,
    filename: str,
    path: Path,
    active_model: Model | None,
    image_pipeline_config: ImagePipelineConfig | None,
) -> list[MessageFragment]:
    """Build the header-text-plus-`image_url` fragment pair for an @mentioned file recognized as
    an image, resized/transcoded for *active_model* via `klorb.images.prepare.
    prepare_image_for_model` exactly like a drag-drop/paste attachment. Falls back to a single
    error-text fragment (`_mention_read_error_result`) if *active_model* has no vision
    capability, *image_pipeline_config* wasn't supplied, the file can't be read, or the prepared
    image is too large/unreadable as an image."""
    if (active_model is None or image_pipeline_config is None
            or not active_model.capabilities().get("vision")):
        logger.debug(
            "@mention %r looks like an image but the active model has no vision support",
            filename)
        return [MessageFragment(type="text", text=_format_attachment(
            ordinal, filename,
            _mention_read_error_result("the active model does not support image input")))]

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        logger.debug("@mention image read failed for %s: %s", filename, exc)
        return [MessageFragment(
            type="text", text=_format_attachment(ordinal, filename, _mention_read_error_result(exc)))]

    try:
        prepared = prepare_image_for_model(raw_bytes, active_model, image_pipeline_config)
    except (ImageTooLargeError, OSError) as exc:
        logger.debug("@mention image prepare failed for %s: %s", filename, exc)
        return [MessageFragment(
            type="text", text=_format_attachment(ordinal, filename, _mention_read_error_result(exc)))]

    logger.info(
        "Resolved @mention %r as image attachment id=%d (%dx%d -> %dx%d, %s)",
        filename, ordinal, prepared.original_width, prepared.original_height,
        prepared.width, prepared.height, prepared.mime_type,
    )
    return [
        MessageFragment(type="text", text=_image_attachment_header_text(ordinal, filename, prepared)),
        MessageFragment(
            type="image_url",
            image_url={"url": f"data:{prepared.mime_type};base64,{prepared.data_b64}"},
            mime_type=prepared.mime_type,
            source_filename=filename,
            original_width=prepared.original_width,
            original_height=prepared.original_height,
            resized_width=prepared.width,
            resized_height=prepared.height,
        ),
    ]


def _resolve_and_read(
    filename: str,
    read_file_core: "ReadFileCore",
    workspace_path: Path,
) -> dict[str, Any]:
    """Resolve *filename* to an absolute path within *workspace_path* and read it as text.

    Returns a `ReadFileCore.apply`-shaped dict on success, or a synthetic error dict on failure.
    No permission check is performed -- the user implicitly authorized the read by @mentioning the
    file.
    """
    path = _resolve_mention_path(filename, workspace_path)
    logger.debug("Reading @mention %r resolved to path %s", filename, path)

    try:
        result = read_file_core.apply(path, {})
    except (OSError, ValueError) as exc:
        logger.debug("@mention read failed for %s: %s", filename, exc)
        return _mention_read_error_result(exc)
    logger.debug(
        "@mention read %s: lines %d-%d of %d (truncated=%s)",
        filename, result["start_line"], result["end_line"],
        result["total_lines"], result["truncated"],
    )
    return result
