# © Copyright 2026 Aaron Kimball
"""Widget/DOM ids, label strings, and other constants shared by two or more `klorb.tui` modules."""

from klorb.session import PermissionFramework

HISTORY_ID = "history"
INTERACTION_PANEL_ID = "interaction-panel"
PROMPT_INPUT_ID = "prompt-input"
PALETTE_HINT_ID = "palette-hint"
STATUS_BAR_ID = "status-bar"
OUTPUT_TOKENS_ID = "output-tokens"
PERMISSION_BADGE_ID = "permission-badge"
SESSION_NAME_ID = "session-name"
TASK_SIDEBAR_ID = "task-sidebar"
SUBAGENTS_PANEL_ID = "subagents-panel"
SUBAGENT_HISTORY_ID = "subagent-history"
SUBAGENT_ATTENTION_STATUS_ID = "subagent-attention-status"

PERMISSION_FRAMEWORK_CYCLE: tuple[PermissionFramework, ...] = ("ask", "auto", "deny")
"""The order Shift+Tab cycles `Session.config.permission_framework` through."""

NEW_SESSION_LABEL = "New session..."
"""Text shown on the session-name status line before the session title is resolved."""
