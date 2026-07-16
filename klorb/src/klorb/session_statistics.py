# © Copyright 2026 Aaron Kimball
"""Running statistics for a `Session`, updated incrementally as messages arrive and tool
calls complete. Persisted alongside the session state so a restored session picks up where
the previous one left off. See docs/specs/session-statistics.md (future).
"""


from pydantic import BaseModel, Field


class ToolCallStats(BaseModel):
    """Per-tool success/failure counts, accumulated across the session's lifetime."""

    success_count: int = 0
    failed_count: int = 0


class SessionStatistics(BaseModel):
    """Running tally of message and tool-call activity for one `Session`.

    Updated incrementally by `Session` as turns flow through `send_turn()` /
    `_send_and_receive()` / `_run_tool_calls()`, and persisted alongside the session state
    (see `klorb.workspace.last_session.LastSessionState`) so a restored session continues
    from where the previous one left off rather than re-deriving counts from the message
    history.
    """

    user_messages: int = 0
    """Number of `role="user"` prompts sent through `send_turn()`."""

    response_messages: int = 0
    """Number of `role="assistant"` or `role="tool_use"` messages received from the model."""

    thinking_messages: int = 0
    """Number of `role="thinking"` messages received (one per model response that included
    reasoning content)."""

    tool_calls: int = 0
    """Total number of tool calls dispatched (every call the model requested, regardless of
    outcome — successful, failed, unknown tool, or malformed arguments)."""

    tools: dict[str, ToolCallStats] = Field(default_factory=dict)
    """Per-tool-name success/failure breakdown, keyed by `Tool.name()`. A tool's entry is
    created on its first call and accumulated thereafter. A call whose tool didn't exist
    (`NoSuchToolException`) is *not* recorded here — it's counted separately under
    `unknown_tool_calls` — since there's no `Tool` instance to ask `is_success()`."""

    unknown_tool_calls: int = 0
    """Number of tool calls that failed because the requested tool name doesn't exist in the
    `ToolRegistry` (see `klorb.tools.registry.NoSuchToolException`)."""

    malformed_tool_calls: int = 0
    """Number of tool calls whose `arguments` string failed to parse as JSON (a
    `json.JSONDecodeError` before any tool is instantiated)."""


    def format_report(self) -> str:
        """Return a human-readable, multi-line summary suitable for display in the history
        scroll (see `>Show Session Stats` palette command)."""
        lines: list[str] = []
        lines.append("Session Statistics")
        lines.append("=" * 40)
        lines.append(f"  User messages:        {self.user_messages}")
        lines.append(f"  Response messages:    {self.response_messages}")
        lines.append(f"  Thinking messages:    {self.thinking_messages}")
        lines.append(f"  Total tool calls:     {self.tool_calls}")
        lines.append(f"  Unknown tool calls:   {self.unknown_tool_calls}")
        lines.append(f"  Malformed tool calls: {self.malformed_tool_calls}")
        if self.tools:
            lines.append("")
            lines.append("  Per-tool breakdown:")
            for tool_name in sorted(self.tools):
                stats = self.tools[tool_name]
                total = stats.success_count + stats.failed_count
                lines.append(
                    f"    {tool_name}: {stats.success_count} succeeded, "
                    f"{stats.failed_count} failed ({total} total)"
                )
        return "\n".join(lines)
