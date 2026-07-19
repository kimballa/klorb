# © Copyright 2026 Aaron Kimball
"""Widget/DOM ids, label strings, and other constants shared by two or more `klorb.tui`
modules. A constant used by exactly one module lives colocated in that module instead.
"""

from klorb.session import PermissionFramework

HISTORY_ID = "history"
INTERACTION_PANEL_ID = "interaction-panel"
PROMPT_INPUT_ID = "prompt-input"
PALETTE_HINT_ID = "palette-hint"
STATUS_BAR_ID = "status-bar"
OUTPUT_TOKENS_ID = "output-tokens"
PERMISSION_BADGE_ID = "permission-badge"
SESSION_NAME_ID = "session-name"

PERMISSION_FRAMEWORK_CYCLE: tuple[PermissionFramework, ...] = ("ask", "auto", "deny")
"""The order Shift+Tab cycles `Session.config.permission_framework` through -- see
`ReplApp.action_cycle_permission_framework`."""

NEW_SESSION_LABEL = "New session..."
"""Text shown on the `SESSION_NAME_ID` status line before the first-turn session-naming
classifier call (`klorb.session_naming`) has resolved for the active `Session` -- the initial
`compose()` value, and what `clear_session()`/`_maybe_restore_last_session()` reset the line
back to whenever they replace the active `Session` with a fresh one."""
