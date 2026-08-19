# © Copyright 2026 Aaron Kimball
"""Shared mechanics for memory tools: namespace/path resolution, filename validation,
and the first-line rule."""

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from klorb.paths import get_klorb_data_dir
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, canonicalize_dir

if TYPE_CHECKING:
    from klorb.tools.setup_context import ToolSetupContext

MEMORIES_DIRNAME = "memories"
"""Directory name holding memory files within either namespace's parent."""

MEMORY_TOC_FILENAME = "MEMORY.md"
"""Reserved filename treated as a table of contents
over that namespace's other memory files."""

MEMORY_TOC_AUTO_READ_LINES = 50
"""How many of `MEMORY_TOC_FILENAME`'s leading lines are
read automatically into the Memories interjection."""

MEMORY_TOC_WARN_LINES = 45
"""Line count at or above which `CreateMemory`/`EditMemory`
attach a warning to their result for `MEMORY_TOC_FILENAME`."""

Namespace = Literal["global", "workspace"]

NAMESPACE_SCHEMA_PROPERTY = {
    "type": "string",
    "enum": ["global", "workspace"],
    "description": (
        "\"global\" memories live under the user's home directory and apply to all "
        "workspaces; \"workspace\" memories live in this workspace's .klorb directory and "
        "apply only here."
    ),
}
"""The `namespace` JSON-schema property shared by every Memory tool's `parameters()`."""


def memory_namespace_dir(context: "ToolSetupContext", namespace: Namespace) -> Path:
    """Return the directory `namespace` resolves to, without creating it."""
    if namespace == "global":
        return get_klorb_data_dir() / MEMORIES_DIRNAME
    return context.session_config.workspace.path / KLORB_PROJECT_DIR_NAME / MEMORIES_DIRNAME


def workspace_namespace_accessible(context: "ToolSetupContext") -> bool:
    """Return whether the `workspace` namespace is accessible."""
    return context.session_config.workspace.trusted


def require_workspace_namespace_accessible(context: "ToolSetupContext", namespace: Namespace) -> None:
    """Raise `PermissionError` if `namespace` is `workspace` and the workspace is untrusted."""
    if namespace == "workspace" and not workspace_namespace_accessible(context):
        raise PermissionError(
            "workspace memories are not accessible in an untrusted workspace")


def validate_memory_filename(filename: str, namespace_dir: Path) -> Path:
    """Validate `filename` and return the `Path` it resolves to within `namespace_dir`.

    Raises `ValueError` if `filename` contains a path separator, doesn't end in `.md`, or
    resolves outside `namespace_dir`. `namespace_dir` need not exist yet.
    """
    if not filename or "/" in filename or "\\" in filename:
        raise ValueError(
            f"filename must not contain a path separator: {filename!r}")
    if not filename.endswith(".md"):
        raise ValueError(f"filename must end in '.md': {filename!r}")

    resolved_dir = namespace_dir.resolve(strict=False)
    candidate = canonicalize_dir(Path(filename), resolved_dir)
    if candidate.parent != resolved_dir:
        raise ValueError(
            f"filename must not escape the memories namespace: {filename!r}")
    return candidate


def validate_first_line_not_blank(content: str, *, subject: str) -> None:
    """Raise `ValueError` if `content`'s first line is empty or all-whitespace."""
    first_line = content.splitlines()[0] if content else ""
    if not first_line.strip():
        raise ValueError(
            f"{subject}'s first line is its topic and must not be blank")


def memory_toc_overflow_warning(namespace: Namespace, filename: str, total_lines: int) -> str | None:
    """Return a warning when `filename` is `MEMORY_TOC_FILENAME` and it now has `total_lines` lines."""
    if filename != MEMORY_TOC_FILENAME or total_lines < MEMORY_TOC_WARN_LINES:
        return None
    return (
        f"{MEMORY_TOC_FILENAME} is now {total_lines} lines long. Only its first "
        f"{MEMORY_TOC_AUTO_READ_LINES} lines are read automatically into your Memories "
        f'interjection each session. Use EditMemory(namespace="{namespace}", '
        f'filename="{MEMORY_TOC_FILENAME}") to compact it down, moving detail into an existing '
        f'memory with EditMemory(namespace="{namespace}", filename="...") or a new one with '
        f'CreateMemory(namespace="{namespace}", filename="...").'
    )


def read_memory_topic(path: Path) -> str:
    """Return `path`'s topic: its first line, or `""` if the file is empty or that line is blank."""
    with open(path, encoding="utf-8") as file:
        first_line = file.readline().splitlines()
    topic = first_line[0] if first_line else ""
    return topic if topic.strip() else ""
