# © Copyright 2026 Aaron Kimball
"""Runs EvalCase tool-efficacy tasks against a real model through a real klorb Session.

See docs/specs/tool-eval-harness.md and docs/adrs/reuse-session-for-tool-eval-agent-loop.md
for why this drives `klorb.session.Session` directly instead of a bespoke chat/tool loop.
"""

import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import klorb.tools as tools_package
from klorb.api_provider import ApiProvider
from klorb.process_config import ProcessConfig
from klorb.session import Session
from klorb.session import SessionConfig
from klorb.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvalCase:
    """One tool-efficacy eval task: a prompt sent to a real model, offered the real klorb file
    tools, plus a deterministic `check` that inspects the resulting workspace file state — never
    the model's closing text (see docs/adrs/grade-tool-evals-by-filesystem-state.md).
    """

    name: str
    prompt: str
    check: Callable[[Path, Session], str | None]
    """Run after the turn completes; given the case's temp `workspace_root` and the `Session`
    that ran it, returns `None` on success or a human-readable failure reason."""
    setup_files: dict[str, str] = field(default_factory=dict)
    """Workspace-relative path -> initial file content, written before `prompt` is sent."""


@dataclass(frozen=True)
class ToolCallLogEntry:
    """One tool call the model made during a case's turn, and the raw result it got back —
    the same `arguments`/response `content` strings that actually crossed the wire, kept
    verbatim (not reparsed) so the eval report can show exactly what the model saw."""

    name: str
    arguments: str
    """Raw JSON-encoded arguments exactly as the model sent them (`ToolCallRequest.arguments`)."""
    response: str | None
    """The matching `role="tool_response"` message's `content`, or `None` if no response was
    ever recorded for this call's id (shouldn't happen in practice — `Session._run_tool_calls`
    always appends one, even on error, but a case aborted mid-round could still leave one
    dangling)."""


@dataclass(frozen=True)
class CaseResult:
    """The outcome of running one `EvalCase` via `run_case()`."""

    name: str
    passed: bool
    duration_s: float
    num_tool_calls: int
    tool_call_counts: dict[str, int]
    tool_call_log: list[ToolCallLogEntry]
    final_response: str
    failure_reason: str | None = None
    """Set when `check()` ran but reported a problem; `None` on success or if `error` is set."""
    error: str | None = None
    """Set instead of running `check()` at all if `session.send_turn()` itself raised."""


def _tool_call_log(session: Session) -> list[ToolCallLogEntry]:
    """Reconstruct the ordered request/response transcript of every tool call the model made
    during `session`'s turn from its message history — `role="tool_use"` messages carry the
    requests, matched up to their `role="tool_response"` counterpart by `ToolCallRequest.id` /
    `Message.tool_call_id`.
    """
    responses_by_id: dict[str, str] = {
        message.tool_call_id: message.content
        for message in session.messages
        if message.role == "tool_response" and message.tool_call_id is not None
    }
    log: list[ToolCallLogEntry] = []
    for message in session.messages:
        if message.role != "tool_use" or not message.tool_calls:
            continue
        for call in message.tool_calls:
            log.append(ToolCallLogEntry(
                name=call.name, arguments=call.arguments, response=responses_by_id.get(call.id)))
    return log


def run_case(
    case: EvalCase, *, model: str, provider: ApiProvider,
    on_start: Callable[[str], None] | None = None,
    on_complete: Callable[[CaseResult], None] | None = None,
) -> CaseResult:
    """Run one `EvalCase` end to end: seed a fresh temp workspace with `case.setup_files`, send
    `case.prompt` through a real `Session` offering the real `klorb.tools` package, then grade
    the result with `case.check()`. See docs/specs/tool-eval-harness.md for the full per-case
    flow.

    If `on_start` is given, it's invoked with `case.name` before the turn is sent; if
    `on_complete` is given, it's invoked with the finished `CaseResult` right before this
    function returns it. Together these let a caller (e.g. the `make evals` CLI) print
    incremental progress — including each tool call's raw request/response, via
    `CaseResult.tool_call_log` — across a run of many cases, without waiting for every case to
    finish first.
    """
    if on_start is not None:
        on_start(case.name)
    with tempfile.TemporaryDirectory(prefix="klorb-eval-") as workspace_dir:
        workspace_root = Path(workspace_dir)
        for relative_path, content in case.setup_files.items():
            file_path = workspace_root / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        session_config = SessionConfig(
            model=model, interactive=False, thinking_enabled=False, workspace_root=workspace_root)
        tool_registry = ToolRegistry(ProcessConfig(), session_config, package=tools_package)
        session = Session(session_config, provider=provider, tool_registry=tool_registry)

        start = time.monotonic()
        final_response = ""
        error: str | None = None
        try:
            final_response = session.send_turn(case.prompt)
        except Exception as exc:
            logger.warning("Eval case %r raised while running: %s", case.name, exc)
            error = f"{type(exc).__name__}: {exc}"
        duration_s = time.monotonic() - start

        tool_call_log = _tool_call_log(session)
        tool_call_counts: dict[str, int] = {}
        for entry in tool_call_log:
            tool_call_counts[entry.name] = tool_call_counts.get(entry.name, 0) + 1

        failure_reason: str | None = None
        if error is None:
            failure_reason = case.check(workspace_root, session)

        result = CaseResult(
            name=case.name,
            passed=error is None and failure_reason is None,
            duration_s=duration_s,
            num_tool_calls=len(tool_call_log),
            tool_call_counts=tool_call_counts,
            tool_call_log=tool_call_log,
            final_response=final_response,
            failure_reason=failure_reason,
            error=error,
        )
        if on_complete is not None:
            on_complete(result)
        return result


def run_evaluation(
    cases: list[EvalCase], *, model: str, provider: ApiProvider,
    on_case_start: Callable[[str], None] | None = None,
    on_case_complete: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Run every case in `cases` in sequence and return their `CaseResult`s in order.

    `on_case_start`/`on_case_complete`, if given, are forwarded to each `run_case()` call as
    `on_start`/`on_complete`.
    """
    return [
        run_case(
            case, model=model, provider=provider, on_start=on_case_start, on_complete=on_case_complete)
        for case in cases
    ]
