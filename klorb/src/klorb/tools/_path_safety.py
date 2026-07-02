# © Copyright 2026 Aaron Kimball
"""Shared path-resolution guard for file-editing tools.

Not a `Tool` itself (leading underscore keeps `pkgutil.iter_modules`-based discovery in
`klorb.tools.registry.ToolRegistry` from mistaking it for one, though only concrete `Tool`
subclasses are actually collected regardless), just a helper `EditFile`, `ReplaceAll`, and
`CreateFile` all call through before touching the filesystem. See
docs/adrs/confine-file-tools-to-workspace-root.md.
"""

from pathlib import Path

from klorb.tools.setup_context import ToolSetupContext


def resolve_within_workspace(context: ToolSetupContext, filename: str) -> Path:
    """Resolve `filename` against `context.session_config.workspace_root` and verify the
    result is the workspace root or somewhere beneath it.

    `filename` is joined onto the workspace root first if it's relative. Both the root and the
    candidate are resolved with `Path.resolve(strict=False)`, which canonicalizes `..`
    segments and symlinks without requiring the target to exist yet (needed for `CreateFile`).
    This means a symlink hop or a `../../` traversal can't be used to reach a path outside the
    workspace root even though the check itself only compares final, resolved paths.

    Raises `PermissionError` if the resolved path falls outside the workspace root.
    """
    root = context.session_config.workspace_root.resolve()
    candidate = Path(filename)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)

    if not resolved.is_relative_to(root):
        raise PermissionError(
            f"{filename!r} resolves to {resolved}, which is outside the permitted "
            f"workspace root {root}")

    return resolved
