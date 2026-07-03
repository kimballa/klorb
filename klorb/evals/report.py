# © Copyright 2026 Aaron Kimball
"""Renders a list of CaseResults from klorb.evals.harness as a markdown eval report."""

from .colors import colorize
from .harness import CaseResult


def render_report(results: list[CaseResult], *, color: bool = False) -> str:
    """Render `results` as a markdown report: a summary followed by one section per case.

    `color`, if set, wraps each case's `[PASS]`/`[FAIL]` marker in ANSI color codes (see
    `klorb.evals.colors`) — leave unset when the report is being written to a file or
    non-terminal stream.
    """
    passed = sum(1 for result in results if result.passed)
    total = len(results)
    total_duration_s = sum(result.duration_s for result in results)
    total_tool_calls = sum(result.num_tool_calls for result in results)
    average_tool_calls = total_tool_calls / total if total else 0.0

    accuracy_suffix = f" ({100 * passed / total:.1f}%)" if total else ""
    lines = [
        "# Tool eval report",
        "",
        "## Summary",
        "",
        f"- **Passed**: {passed}/{total}{accuracy_suffix}",
        f"- **Total duration**: {total_duration_s:.2f}s",
        f"- **Total tool calls**: {total_tool_calls}",
        f"- **Average tool calls per case**: {average_tool_calls:.2f}",
        "",
        "## Cases",
        "",
    ]
    for result in results:
        status = colorize("PASS", "green", enabled=color) if result.passed \
            else colorize("FAIL", "red", enabled=color)
        lines.append(f"### [{status}] {result.name}")
        lines.append("")
        lines.append(f"- **Duration**: {result.duration_s:.2f}s")
        lines.append(f"- **Tool calls**: {result.num_tool_calls} {result.tool_call_counts}")
        if result.error is not None:
            lines.append(f"- **Error**: {result.error}")
        elif result.failure_reason is not None:
            lines.append(f"- **Failure reason**: {result.failure_reason}")
        lines.append(f"- **Final response**: {result.final_response!r}")
        lines.append("")
    return "\n".join(lines)
