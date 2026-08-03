# © Copyright 2026 Aaron Kimball
"""Shared constant for the `CreateSubagent`/`WaitForSubagent`/`MessageSubagent` tools."""

SUBAGENT_TOOL_CATEGORY = "SUBAGENT"
"""`Tool.category()` value shared by `CreateSubagent`/`WaitForSubagent`/`MessageSubagent`, so a
role's `restrict_to.tool_categories` can admit (or exclude) all three together."""
