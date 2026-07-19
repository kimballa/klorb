# © Copyright 2026 Aaron Kimball
"""Renders a list of CaseResults from klorb.evals.harness as a markdown eval report."""

from .colors import colorize
from .harness import CaseResult


def status_label(result: CaseResult, *, color: bool) -> str:
    """Render `result`'s status: `FAIL` (red) if it didn't pass; `CONDITIONAL PASS` (yellow) if
    it passed but either used more tool calls than `EvalCase.expected_tool_calls` allowed for or
    its `EvalCase.soft_check` flagged a suboptimal-but-correct call shape (see
    `CaseResult.conditional` and docs/adrs/eval-conditional-pass-on-excess-tool-calls.md);
    plain `PASS` (green) otherwise. Shared by `render_report()` and `run_evals`'s per-case
    progress printer so both use the same three-way status.
    """
    if not result.passed:
        return colorize("FAIL", "red", enabled=color)
    if result.conditional:
        return colorize("CONDITIONAL PASS", "yellow", enabled=color)
    return colorize("PASS", "green", enabled=color)


def render_summary(results: list[CaseResult]) -> str:
    """Render just the `## Summary` section of the eval report: pass/fail counts, conditional
    passes, and aggregate duration/tool-call stats. Shared by `render_report()` (as its opening
    section) and `run_evals`'s `evals.log` writer, which uses this alone as the log's entire
    contents on a fully clean run (see docs/specs/tool-eval-harness.md).
    """
    passed = sum(1 for result in results if result.passed)
    conditional = sum(1 for result in results if result.conditional)
    total = len(results)
    total_duration_s = sum(result.duration_s for result in results)
    total_tool_calls = sum(result.num_tool_calls for result in results)
    average_tool_calls = total_tool_calls / total if total else 0.0

    accuracy_suffix = f" ({100 * passed / total:.1f}%)" if total else ""
    lines = [
        "## Summary",
        "",
        f"- **Passed**: {passed}/{total}{accuracy_suffix}",
        f"- **Conditional passes**: {conditional} (passed, but used more tool calls than expected, "
        "or a soft check flagged the call shape)",
        f"- **Total duration**: {total_duration_s:.2f}s",
        f"- **Total tool calls**: {total_tool_calls}",
        f"- **Average tool calls per case**: {average_tool_calls:.2f}",
    ]
    return "\n".join(lines)


def render_case_detail(result: CaseResult, *, color: bool) -> str:
    """Render one case's tool-call transcript (request/response pairs, tool call counts, and
    pass/fail status line) — the same detail `run_evals`'s per-case progress printer shows as
    each case finishes, factored out here so it can also be written verbatim (with `color=False`)
    to `evals.log` for cases that failed or conditionally passed.

    A FAILed case's `error`/`failure_reason` is included (same precedence as `render_report()`:
    `error` first, since it means `check()` never ran at all, then `failure_reason`, then
    `soft_failure_reason`) — `evals.log` is the transcript `run_evals.py --self-review` feeds
    back to a model asked to diagnose *why* cases failed, so this detail is exactly what that
    diagnosis needs.
    """
    lines: list[str] = []
    for entry in result.tool_call_log:
        failed = (entry.response or "").startswith("Error:")
        lines.append(colorize(f"  -> {entry.name}({entry.arguments})", "yellow", enabled=color))
        response_color = "red" if failed else "green"
        lines.append(colorize(f"  <- {entry.response}", response_color, enabled=color))
    lines.append(f"- **Tool call counts**: {result.num_tool_calls} {result.tool_call_counts}")
    lines.append(f"- **Generated tokens (est.)**: {result.generated_tokens}")
    if result.error is not None:
        lines.append(f"- **Error**: {result.error}")
    elif result.failure_reason is not None:
        lines.append(f"- **Failure reason**: {result.failure_reason}")
    elif result.soft_failure_reason is not None:
        lines.append(f"- **Soft-check reason**: {result.soft_failure_reason}")
    status = status_label(result, color=color)
    lines.append(f"  [{status}] {result.name} ({result.duration_s:.2f}s)")
    return "\n".join(lines)


def render_report(
    results: list[CaseResult], *, color: bool = False,
    tool_token_counts: dict[str, int] | None = None,
) -> str:
    """Render `results` as a markdown report: a summary, an optional per-tool token-count
    breakdown, then one section per case.

    `color`, if set, wraps each case's `[PASS]`/`[FAIL]`/`[CONDITIONAL PASS]` marker in ANSI
    color codes (see `klorb.evals.colors`) — leave unset when the report is being written to a
    file or non-terminal stream. `tool_token_counts`, if given (see
    `klorb.evals.harness.tool_token_counts()`), adds a "Tool definitions" section: each
    offered tool's name and the token count of its full function-calling definition — the
    fixed per-turn prompt cost of offering that tool, independent of any case.
    """
    lines = ["# Tool eval report", "", render_summary(results), ""]
    if tool_token_counts:
        lines.append("## Tool definitions")
        lines.append("")
        lines.append(
            "Token count of each tool's full function-calling definition (name + description "
            "+ parameters schema) — the fixed cost of offering that tool on every turn, "
            "regardless of which case exercises it:")
        lines.append("")
        total_tokens = 0
        for name, tokens in sorted(tool_token_counts.items(), key=lambda kv: kv[1], reverse=True):
            total_tokens += tokens
            lines.append(f"- **{name}**: {tokens} tokens")
        lines.append("")
        lines.append(f"Total tokens in tools payload: {total_tokens}")
        lines.append("")
    lines.append("## Cases")
    lines.append("")
    for result in results:
        status = status_label(result, color=color)
        lines.append(f"### [{status}] {result.name}")
        lines.append("")
        lines.append(f"- **Duration**: {result.duration_s:.2f}s")
        expected_suffix = (
            f" (expected <= {result.expected_tool_calls})"
            if result.expected_tool_calls is not None else "")
        lines.append(
            f"- **Tool calls**: {result.num_tool_calls}{expected_suffix} {result.tool_call_counts}")
        lines.append(f"- **Generated tokens (est.)**: {result.generated_tokens}")
        if result.error is not None:
            lines.append(f"- **Error**: {result.error}")
        elif result.failure_reason is not None:
            lines.append(f"- **Failure reason**: {result.failure_reason}")
        elif result.soft_failure_reason is not None:
            lines.append(f"- **Soft-check reason**: {result.soft_failure_reason}")
        lines.append(f"- **Final response**: {result.final_response!r}")
        lines.append("")
    return "\n".join(lines)
