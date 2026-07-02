# © Copyright 2026 Aaron Kimball
"""Configuration handed to every `Tool` at construction time."""

from pydantic import BaseModel

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig


class ToolSetupContext(BaseModel):
    """Everything a `Tool` needs to configure itself, passed to its constructor in place of
    tool-specific constructor arguments (see `klorb.tools.tool.Tool`).

    Holds references to the process-wide and session-scoped config a `Tool` might need to
    read from (e.g. `process_config.read_file_max_lines`), rather than pre-extracting the
    individual settings each tool happens to care about today — a new tool that needs a new
    setting reads it straight off one of these, no `ToolSetupContext` change required.
    """

    process_config: ProcessConfig
    session_config: SessionConfig
    """The active `Session.config` — *not* `process_config.session`, which is only the
    template a new session's config is copied from (see docs/specs/process-and-session-config.md)
    and won't reflect changes made to the live session (e.g. via the TUI command palette)."""
