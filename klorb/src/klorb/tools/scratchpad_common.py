# © Copyright 2026 Aaron Kimball
"""Shared helper for the `ScratchpadRead`/`ScratchpadWrite`/`ScratchpadSearch` tools: resolving
the active session's scratchpad file path (see `klorb.session.Session.scratchpad_path`).
"""

from pathlib import Path

from klorb.tools.setup_context import ToolSetupContext


def scratchpad_path(context: ToolSetupContext) -> Path:
    """Return the active session's scratchpad file path.

    Raises `ValueError` if `context` wasn't built with a real `Session` (e.g. a `ToolSetupContext`
    constructed directly, as most unit tests for other tools do) -- the scratchpad is per-session
    state, so a Scratchpad* tool has nothing to read or write without one.
    """
    if context.session is None:
        raise ValueError("Scratchpad tools require an active session")
    return context.session.scratchpad_path
