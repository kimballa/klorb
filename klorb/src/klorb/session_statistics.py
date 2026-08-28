# © Copyright 2026 Aaron Kimball
"""Running statistics for a `Session`, updated incrementally as messages arrive and tool
calls complete.
"""


from pydantic import BaseModel, Field

SERVER_TOOL_COST_PER_CALL_USD: dict[str, float] = {"web_search_requests": 0.007}
"""Estimated per-call cost of a provider-reported server-tool usage-block counter (see
`ProviderResponse.server_tool_calls`), keyed the same way OpenRouter names that counter. Used
only for the itemized display in `format_report()` -- the actual billed amount is always
`SessionStatistics.total_cost`, which OpenRouter already reports inclusive of any server-tool
surcharge."""


class ToolCallStats(BaseModel):
    """Per-tool success/failure counts, accumulated across the session's lifetime."""

    success_count: int = 0
    failed_count: int = 0


class SessionStatistics(BaseModel):
    """Running tally of message and tool-call activity for one `Session`. Persisted alongside
    the session state so a restored session continues from where the previous one left off.
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
    created on its first call and accumulated thereafter."""

    unknown_tool_calls: int = 0
    """Number of tool calls that failed because the requested tool name doesn't exist in the
    `ToolRegistry`."""

    malformed_tool_calls: int = 0
    """Number of tool calls whose `arguments` string failed to parse as JSON (a
    `json.JSONDecodeError` before any tool is instantiated)."""

    input_tokens: int = 0
    """Aggregate input (prompt) tokens billed across all requests in this session."""

    output_tokens: int = 0
    """Aggregate output (completion) tokens billed across all requests in this session."""

    cached_tokens: int = 0
    """Aggregate input tokens served from the provider prompt cache across all requests."""

    total_cost: float = 0.0
    """Aggregate monetary cost across all requests in this session. Zero when the
    provider does not report cost."""

    server_tool_calls: dict[str, int] = Field(default_factory=dict)
    """Aggregate per-type server-tool call counts across the session (e.g.
    `{"web_search_requests": 5}`), from `ProviderResponse.server_tool_calls`."""

    def record_usage(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        cost: float = 0.0,
        server_tool_calls: dict[str, int] | None = None,
    ) -> None:
        """Accumulate one request's token usage into the session totals."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_tokens += cached_tokens
        self.total_cost += cost
        for key, count in (server_tool_calls or {}).items():
            self.server_tool_calls[key] = self.server_tool_calls.get(key, 0) + count

    def format_report(self) -> str:
        """Return a human-readable, multi-line summary suitable for display in the history
        scroll."""
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
        if self.server_tool_calls:
            lines.append("")
            lines.append("  Server tool calls:")
            for key in sorted(self.server_tool_calls):
                count = self.server_tool_calls[key]
                cost_per_call = SERVER_TOOL_COST_PER_CALL_USD.get(key)
                estimate = f" (~${count * cost_per_call:.3f})" if cost_per_call else ""
                lines.append(f"    {key}: {count}{estimate}")
        # --- token usage ---
        lines.append("")
        lines.append("Token Usage")
        lines.append("-" * 40)
        total_all_tokens = self.input_tokens + self.output_tokens

        cache_pct = ((100.0 * self.cached_tokens) / self.input_tokens) if self.input_tokens > 0 else 0.0

        uncached_tokens = max(self.input_tokens - self.cached_tokens, 0)
        in_out_tokens = uncached_tokens + self.output_tokens

        # Format numbers with commas
        input_str = f"{self.input_tokens:,}"
        cached_str = f"{self.cached_tokens:,}"
        uncached_str = f"{uncached_tokens:,}"
        output_str = f"{self.output_tokens:,}"
        total_str = f"{total_all_tokens:,}"
        in_out_str = f"{in_out_tokens:,}"
        cost_str = f"${self.total_cost:.3f}"

        # Right-align numbers to the widest one
        max_width = max(len(input_str),
                        len(cached_str),
                        len(uncached_str),
                        len(output_str),
                        len(total_str),
                        len(in_out_str),
                        len(cost_str))
        label_w = 18

        lines.append(f"  {'Input tokens:':<{label_w}}{input_str:>{max_width}}")
        lines.append(f"  {'Cached tokens:':<{label_w}}{cached_str:>{max_width}} ({cache_pct:.1f}%)")
        lines.append(f"  {'Uncached tokens:':<{label_w}}{uncached_str:>{max_width}}")
        lines.append(f"  {'Output tokens:':<{label_w}}{output_str:>{max_width}}")
        lines.append(f"  {'-' * (label_w + max_width)}")
        lines.append(f"  {'Total tokens:':<{label_w}}{total_str:>{max_width}}")
        lines.append(f"  {'In+out tokens:':<{label_w}}{in_out_str:>{max_width}}")
        lines.append("")
        lines.append(f"  {'Cost:':<{label_w}}{cost_str:>{max_width}}")
        return "\n".join(lines)
