# © Copyright 2026 Aaron Kimball
"""Configuration handed to every `Tool` at construction time."""

from pydantic import BaseModel, ConfigDict

from klorb.permissions.table import PermissionOverride
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig


class ToolSetupContext(BaseModel):
    """Everything a `Tool` needs to configure itself, passed to its constructor in place of
    tool-specific constructor arguments (see `klorb.tools.tool.Tool`).

    Holds references to the process-wide and session-scoped config a `Tool` might need to
    read from (e.g. `process_config.read_file_max_lines`), rather than pre-extracting the
    individual settings each tool happens to care about today — a new tool that needs a new
    setting reads it straight off one of these, no `ToolSetupContext` change required.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    process_config: ProcessConfig
    session_config: SessionConfig
    """The active `Session.config` — *not* `process_config.session`, which is only the
    template a new session's config is copied from (see docs/specs/process-and-session-config.md)
    and won't reflect changes made to the live session (e.g. via the TUI command palette)."""
    session: Session | None = None
    """The active `Session` itself, so a `Tool` can read/write `session.tool_state` for its own
    per-session runtime bookkeeping (e.g. `BashTool`'s one-time sandbox-fallback notice) —
    distinct from `session_config`, which only covers user-configurable settings, not ad hoc
    tool-private state. `None` for a `ToolSetupContext` built without a real `Session` (most
    unit tests construct one directly); a `Tool` that uses `session.tool_state` must handle that
    case gracefully rather than assuming it's always set. Set on `ToolRegistry` post-construction
    by `Session.__init__` (`ToolRegistry` is always built before the `Session` it's passed into,
    so this can't be a `ToolRegistry` constructor argument) and threaded into every
    `ToolSetupContext` `ToolRegistry` builds from then on."""
    permission_override: PermissionOverride | None = None
    """When set, the resources this one `Tool` instance is permitted to bypass the
    `readDirs`/`writeDirs`/`commandRules` tables for — one or more one-shot "Allow (once)"
    grants (see `Session.PermissionDecision`, `klorb.permissions.table.PermissionOverride`) that
    persist no table entry, so the identical access asks again next time. Never bypasses the
    unconditional `is_privileged_path()` deny — see
    `klorb.permissions.workspace.evaluate_write`/`resolve_and_evaluate_read`."""
