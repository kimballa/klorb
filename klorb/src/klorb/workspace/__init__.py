# © Copyright 2026 Aaron Kimball
"""The `klorb.workspace` package: everything to do with what klorb considers the current
project root, whether it's a registered project, and whether the user has trusted it. See
docs/specs/projects-and-trust.md.

This top-level module defines `Workspace` itself (the value object every other module in this
package, and `ProcessConfig.workspace`, revolves around) and re-exports `TrustManager`
(`klorb.workspace.trust_manager`, which owns `projects.json`) for callers outside this package
to import directly (`from klorb.workspace import TrustManager`). `klorb.workspace.workspace_init`
(the config-file writers used once a workspace is opened as a project or newly trusted) is
deliberately *not* re-exported here: it imports `klorb.process_config` for the schema/key
constants a `klorb-config.json` write needs, and `klorb.process_config` itself imports
`Workspace` from this module — re-exporting `workspace_init` here too would make that a real
import cycle (`process_config` -> `workspace` -> `workspace_init` -> `process_config`) rather
than the one-way dependency it is today. Callers that need `write_initial_project_config`/
`write_session_defaults_to_project_config` (currently just `klorb.tui.repl`) import them from
`klorb.workspace.workspace_init` directly. Code within this package (`trust_manager.py`, which
has no such dependency on `klorb.process_config`) imports `Workspace` back from here via a
relative import, per this repo's own import-style rule for same-feature imports.

A `Workspace` is attached to `ProcessConfig.workspace` (`klorb.process_config`) once resolved
(`TrustManager.resolve_workspace`), and is never loaded from `klorb-config.json` — a project
must not be able to grant itself trust, or fabricate its own project id, via its own config
file. `workspace.trusted` is read directly by the permission-evaluation code
(`klorb.permissions.workspace.evaluate_write`/`resolve_and_evaluate_read`).
(See docs/adrs/consolidate-workspace-trust-into-a-single-field.md).
"""

from pathlib import Path

from pydantic import BaseModel


class Workspace(BaseModel):
    """One resolved project root and what klorb knows about it.

    `id` is the project's uuid4 key into `projects.json`
    (`klorb.workspace.trust_manager.ProjectRecord`), or `None` if this workspace has no
    persistent record yet — either because the user declined to open it as a project, or
    because it hasn't been resolved/bootstrapped at all yet. `is_project` is `True` exactly
    when `id` is not `None`; kept as its own field (rather than derived) so it round-trips the
    same way through `model_copy()`/equality checks as every other field here, and so a reader
    doesn't have to know `id is not None` is the invariant. `trusted` (see module docstring)
    governs `ReadFile`'s workspace-boundary behavior regardless of `is_project` — an
    unregistered workspace can still be trusted for the rest of this process's lifetime, it
    just won't be remembered next time klorb runs there.
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
