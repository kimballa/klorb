# © Copyright 2026 Aaron Kimball
"""Shared mechanics behind `ListMemories`/`SearchMemories`/`ReadMemory`/`EditMemory`/
`CreateMemory`/`ForgetMemory`: resolving a `namespace`/`filename` pair to a `Path`, validating
`filename` against path-separator/traversal escapes, the "first line must not be blank"
file-format rule, and the untrusted-workspace access gate. See docs/specs/memories.md.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from klorb.paths import KLORB_DATA_DIR
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, canonicalize_dir

if TYPE_CHECKING:
    from klorb.tools.setup_context import ToolSetupContext

MEMORIES_DIRNAME = "memories"
"""Directory name holding memory files within either namespace's parent (`KLORB_DATA_DIR` for
`global`, `${workspace_root}/.klorb` for `workspace`) — see `memory_namespace_dir()`."""

Namespace = Literal["global", "workspace"]

NAMESPACE_SCHEMA_PROPERTY = {
    "type": "string",
    "enum": ["global", "workspace"],
    "description": (
        "\"global\" memories live under the user's home directory and apply across every "
        "workspace; \"workspace\" memories live in this workspace's .klorb directory and "
        "apply only here."
    ),
}
"""The `namespace` JSON-schema property shared by every Memory tool's `parameters()`."""


def memory_namespace_dir(context: "ToolSetupContext", namespace: Namespace) -> Path:
    """Return the directory `namespace` resolves to, without creating it -- both namespace
    directories are created on demand, at first write, by `CreateFileCore.apply()`'s own
    `path.parent.mkdir(parents=True, exist_ok=True)`, not eagerly here."""
    if namespace == "global":
        return KLORB_DATA_DIR / MEMORIES_DIRNAME
    return context.session_config.workspace.path / KLORB_PROJECT_DIR_NAME / MEMORIES_DIRNAME


def workspace_namespace_accessible(context: "ToolSetupContext") -> bool:
    """Return whether the `workspace` namespace is accessible at all -- gated on workspace
    trust, exactly like `ReadFile`'s own workspace-boundary behavior (see
    `klorb.workspace.Workspace.trusted`). The `global` namespace is never affected by this."""
    return context.session_config.workspace.trusted


def require_workspace_namespace_accessible(context: "ToolSetupContext", namespace: Namespace) -> None:
    """Raise `PermissionError` outright -- with no appeal to the user, unlike an `ask`-verdict
    permission check -- if `namespace` is `workspace` and the workspace is untrusted. Called by
    `ReadMemory`/`EditMemory`/`CreateMemory`/`ForgetMemory` ahead of their own
    `tools.memory.*Permission` check, so a workspace memory in an untrusted workspace is denied
    before that check (and before any disk I/O) rather than surfacing as a `PermissionAskRequired`.
    `ListMemories`/`SearchMemories` do not call this: per docs/specs/memories.md, they instead
    report the `workspace` namespace as empty/unsearched rather than raising.
    """
    if namespace == "workspace" and not workspace_namespace_accessible(context):
        raise PermissionError(
            "workspace memories are not accessible in an untrusted workspace")


def validate_memory_filename(filename: str, namespace_dir: Path) -> Path:
    """Validate `filename` and return the `Path` it resolves to within `namespace_dir`.

    Raises `ValueError` if `filename` contains a path separator, doesn't end in `.md`, or
    resolves (via `..`, a symlink, or otherwise) outside `namespace_dir` -- rejected rather than
    silently normalized, so the name a caller passes is always the name that's actually used.
    `namespace_dir` need not exist yet.
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
    """Raise `ValueError` if `content`'s first line is empty or all-whitespace -- the file
    format rule that a memory's first line is its topic, and so must never be blank. `subject`
    names what's being validated, for the error message (e.g. a memory's `namespace`/`filename`).
    """
    first_line = content.splitlines()[0] if content else ""
    if not first_line.strip():
        raise ValueError(
            f"{subject}'s first line is its topic and must not be blank")


def read_memory_topic(path: Path) -> str:
    """Return `path`'s topic: its first line, or `""` if the file is empty or that line is
    blank/whitespace-only -- used by `ListMemoriesTool`/`SearchMemoriesTool`.
    """
    with open(path, encoding="utf-8") as file:
        first_line = file.readline().splitlines()
    topic = first_line[0] if first_line else ""
    return topic if topic.strip() else ""
