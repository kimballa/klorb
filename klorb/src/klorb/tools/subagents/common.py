# © Copyright 2026 Aaron Kimball
"""Constants for the subagent tools."""

SUBAGENT_TOOL_CATEGORY = "SUBAGENT"
"""`Tool.category()` value for the subagent lifecycle tools (`CreateSubagent`/
`WaitForSubagent`), so a role's `restrict_to.tool_categories` can admit or exclude them together."""

MESSAGING_TOOL_CATEGORY = "MESSAGING"
"""`Tool.category()` value for the agent-messaging tools (`SendMessage`/`GetMessages`), so a
role's `restrict_to.tool_categories` can admit or exclude them separately from subagent
lifecycle management."""
