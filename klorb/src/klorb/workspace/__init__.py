# © Copyright 2026 Aaron Kimball
"""The `klorb.workspace` package: everything to do with what klorb considers the current
project root, whether it's a registered project, and whether the user has trusted it.
See docs/specs/projects-and-trust.md.
"""

from pathlib import Path

from pydantic import BaseModel


class Workspace(BaseModel):
    """One resolved project root and what klorb knows about it.

    `id` is the project's uuid4 key into `projects.json`, or `None` if this workspace has no
    persistent record yet. `is_project` is `True` exactly when `id` is not `None`; kept as its
    own field so it round-trips the same way through `model_copy()`/equality checks as every
    other field here, and so a reader doesn't have to know `id is not None` is the invariant.
    `trusted` governs `ReadFile`'s workspace-boundary behavior regardless of `is_project`.
    """

    id: str | None = None
    path: Path
    is_project: bool = False
    trusted: bool = False


# Imported after `Workspace` is defined above, not at module top: `trust_manager` imports
# `Workspace` back from this package (`from klorb.workspace import Workspace`), so re-exporting
# it here before `Workspace` exists on this module would be a circular import.
from klorb.workspace.trust_manager import TrustManager  # noqa: E402

__all__ = [
    "TrustManager",
    "Workspace",
]
