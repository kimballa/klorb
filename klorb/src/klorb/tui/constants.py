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

PERMISSION_FRAMEWORK_CYCLE: tuple[PermissionFramework, ...] = ("ask", "auto", "deny")
"""The order Shift+Tab cycles `Session.config.permission_framework` through -- see
`ReplApp._cycle_permission_framework`."""
