# © Copyright 2026 Aaron Kimball
"""Runs EvalCase tool-efficacy tasks against a real model through a real klorb Session.

See docs/specs/tool-eval-harness.md and docs/adrs/00032-reuse-session-for-tool-eval-agent-loop.md
for why this drives `klorb.session.Session` directly instead of a bespoke chat/tool loop.
"""

import json
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import tiktoken

import klorb.tools as tools_package
import klorb.tools.memory.common as memory_common_module
from klorb.agents.policy import compute_root_session_grants
from klorb.api_provider import ApiProvider
from klorb.message import Message
from klorb.permissions.directory_access import DirRules
from klorb.permissions.skill_access import SkillRules
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, WorkspaceAccess
from klorb.tools.registry import ToolRegistry
from klorb.workspace import Workspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvalCase:
    """One tool-efficacy eval task: a prompt sent to a real model, offered the real klorb file
    tools, plus a deterministic `check` that inspects the resulting workspace file state — never
    the model's closing text (see docs/adrs/00031-grade-tool-evals-by-filesystem-state.md).
    """

    name: str
    prompt: str
    check: Callable[[Path, Session], str | None]
    """Run after the turn completes; given the case's temp `workspace_root` and the `Session`
    that ran it, returns `None` on success or a human-readable failure reason."""
    setup_files: dict[str, str] = field(default_factory=dict)
    """Workspace-relative path -> initial file content, written before `prompt` is sent."""
    workspace_trusted: bool = False
    """Whether the eval workspace is trusted. `False` (the default) matches most cases; a skill
    case sets it `True` so workspace-tier skills under `.klorb/skills/` are discoverable."""
    skill_rules: SkillRules | None = None
    """`skillRules` for the session, so a skill case can pre-`allow` the `(namespace, name)` it
    exercises (a headless eval has no interactive surface to answer an activation ask). `None`
    (the default) leaves the session's rules empty."""
    expected_tool_calls: int | None = None
    """Rough number of tool calls a competent, error-free run should need (e.g. one `ReadFile`
    plus one `EditFile`). Compared against `CaseResult.num_tool_calls` to flag an otherwise
    passing case as a `CaseResult.conditional` pass when the model needed noticeably more calls
    than that — typically a sign it stumbled into retries (e.g. a rejected `EditFile` call)
    before eventually recovering. `None` (the default) means no threshold is checked for this
    case. See docs/adrs/00034-eval-conditional-pass-on-excess-tool-calls.md."""
    soft_check: Callable[[Path, Session], str | None] | None = None
    """Run only when `check` passes (same signature): a non-`None` return marks the result
    `conditional` with that string as `CaseResult.soft_failure_reason`, without flipping
    `passed` to `False` -- for a case whose file-state outcome is correct but whose *shape*
    (which tool-call form the model reached for) is a yellow flag rather than a failure, e.g.
    proving a long-span edit used `old_text_start`/`old_text_end` rather than a token-wasteful
    `old_text`. `None` (the default) means no shape check runs for this case."""
    extra_tool_names: frozenset[str] | None = None
    """Tool names that should be advertised to the model with a full schema for this case even
    though `Tool.default_visible()`/`default_described()` would otherwise omit or summarize
    them -- e.g. `ReplaceAll`. Wired to `ToolRegistry.extra_visible_tools` in `run_case()`."""
    global_memory_files: dict[str, str] = field(default_factory=dict)
    """Bare filename to initial content, written into the `global` memory namespace before
    `prompt` is sent. `run_case()` always points the `global` namespace at a case-private temp
    directory, never the real `KLORB_DATA_DIR`."""


@dataclass(frozen=True)
class EvalSuite:
    """A named group of `EvalCase`s run together as one unit, e.g. `"file-tools"` for the
    `ReadFile`/`CreateFile`/`EditFile`/`ReplaceAll`/`ListDir` cases in `klorb/evals/cases.py`.
    `run_evals.py`'s `--suite` flag selects one suite by `name` (or the special value `"all"`,
    which runs every suite in `klorb.evals.cases.ALL_SUITES`)."""

    name: str
    cases: list[EvalCase]


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
    expected_tool_calls: int | None = None
    """Copied from the `EvalCase.expected_tool_calls` that produced this result."""
    soft_failure_reason: str | None = None
    """Set when `EvalCase.soft_check` ran (only possible when `check` itself passed) and
    reported a problem — see `EvalCase.soft_check` and `conditional` below."""
    generated_tokens: int = 0
    """Informational estimate (not a graded threshold) of how many tokens this case's run
    generated: the case's tool-call `arguments` strings (`tool_call_log`, verbatim) plus
    `final_response`, under the eval model's `tiktoken` encoding (see
    `harness._encoding_for_model()`) -- the closest proxy this harness has to cost without real
    token accounting. Read by report.py's per-case output; never compared against a threshold."""

    @property
    def conditional(self) -> bool:
        """True if this case passed but either took more tool calls than `expected_tool_calls`
        or its `soft_check` flagged a suboptimal-but-correct call shape (`soft_failure_reason`
        set) — a sign the model likely stumbled into a rejected call and retried before
        recovering, or reached for a token-wasteful form, rather than a clean sign of genuine
        failure. See report.py's `[CONDITIONAL PASS]` status and
        docs/adrs/00034-eval-conditional-pass-on-excess-tool-calls.md."""
        return self.passed and (
            (self.expected_tool_calls is not None and self.num_tool_calls > self.expected_tool_calls)
            or self.soft_failure_reason is not None
        )


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


def tool_call_args(session: Session, tool_name: str) -> list[dict]:
    """Every call to `tool_name` the model made during `session`'s turn, as parsed argument
    dicts, in the order they were made -- for an `EvalCase.check`/`soft_check` that needs to
    confirm a specific tool (and, often, a specific argument shape) was actually reached for,
    not just that the workspace ended up in the right state. See docs/adrs/grade-tool-evals-by-
    filesystem-state.md's "where useful" carve-out."""
    return [
        json.loads(call.arguments)
        for message in session.messages
        if message.role == "tool_use" and message.tool_calls
        for call in message.tool_calls
        if call.name == tool_name
    ]


def _wire_payload_text(message: Message) -> str:
    """The actual wire payload text for `message`: `provider_content()`, plus -- for a
    `role="tool_use"` message -- each requested call's name and `wire_arguments()`, the same
    `tool_calls` array a provider request sends alongside `content` and that `provider_content()`
    alone omits."""
    content = message.provider_content()
    body = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    if message.role != "tool_use" or not message.tool_calls:
        return body
    lines = [body] if body else []
    lines.extend(
        f"tool_call id={call.id} name={call.name} arguments={call.wire_arguments()}"
        for call in message.tool_calls)
    return "\n".join(lines)


def _print_message_trace(session: Session) -> None:
    """Dump `session.wire_message_snapshot()` to stdout for `--trace`: one
    `=== message role=<role> id=<n> ===` header per message, followed by that message's actual
    wire payload text (not the persisted, pre-transform `tool_response` envelope JSON) and a
    blank line."""
    for index, message in enumerate(session.wire_message_snapshot()):
        print(f"=== message role={message.role} id={index} ===")
        print(_wire_payload_text(message))
        print()


def run_case(
    case: EvalCase, *, model: str, provider: ApiProvider,
    on_start: Callable[[str], None] | None = None,
    on_complete: Callable[[CaseResult], None] | None = None,
    trace: bool = False,
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

    `trace`, if set, prints every message's actual wire payload text to stdout (via
    `_print_message_trace()`) once the turn finishes.

    The `global` memory namespace is repointed at a case-private temp directory for the
    duration of the case, seeded beforehand from `case.global_memory_files`, so a memory-tool
    case can exercise `global`-namespace reads and writes without touching a real
    `KLORB_DATA_DIR`.
    """
    if on_start is not None:
        on_start(case.name)
    with (
        tempfile.TemporaryDirectory(prefix="klorb-eval-") as workspace_dir,
        tempfile.TemporaryDirectory(prefix="klorb-eval-data-") as data_dir,
    ):
        workspace_root = Path(workspace_dir)
        for relative_path, content in case.setup_files.items():
            file_path = workspace_root / relative_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        isolated_data_dir = Path(data_dir)
        if case.global_memory_files:
            global_memories_dir = isolated_data_dir / "memories"
            global_memories_dir.mkdir(parents=True, exist_ok=True)
            for filename, content in case.global_memory_files.items():
                (global_memories_dir / filename).write_text(content, encoding="utf-8")

        original_get_klorb_data_dir = memory_common_module.get_klorb_data_dir
        memory_common_module.get_klorb_data_dir = lambda: isolated_data_dir
        try:
            session_config = SessionConfig(
                model=model, interactive=False, thinking_enabled=False,
                workspace_access=WorkspaceAccess(
                    workspace=Workspace(path=workspace_root, trusted=case.workspace_trusted),
                    read_dirs=DirRules(allow=[workspace_root]),
                    write_dirs=DirRules(allow=[workspace_root])),
                skill_rules=case.skill_rules if case.skill_rules is not None else SkillRules())
            process_config = ProcessConfig()
            grants = compute_root_session_grants(
                process_config, session_config, session_config.role_name)
            if case.extra_tool_names:
                grants.tool_registry.extra_visible_tools = case.extra_tool_names
            session_config.skill_rules = grants.skill_rules
            session = Session(
                session_config, provider=provider, process_config=process_config,
                tool_registry=grants.tool_registry,
                effective_subagent_roles=grants.effective_subagent_roles)

            start = time.monotonic()
            final_response = ""
            error: str | None = None
            try:
                final_response = session.send_turn(case.prompt)
            except Exception as exc:
                logger.warning("Eval case %r raised while running: %s", case.name, exc)
                error = f"{type(exc).__name__}: {exc}"
            duration_s = time.monotonic() - start
            if trace:
                _print_message_trace(session)

            tool_call_log = _tool_call_log(session)
            tool_call_counts: dict[str, int] = {}
            for entry in tool_call_log:
                tool_call_counts[entry.name] = tool_call_counts.get(entry.name, 0) + 1

            failure_reason: str | None = None
            soft_failure_reason: str | None = None
            if error is None:
                failure_reason = case.check(workspace_root, session)
                if failure_reason is None and case.soft_check is not None:
                    soft_failure_reason = case.soft_check(workspace_root, session)

            encoding = _encoding_for_model(model)
            generated_tokens = len(encoding.encode(final_response))
            for entry in tool_call_log:
                generated_tokens += len(encoding.encode(entry.arguments))

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
                expected_tool_calls=case.expected_tool_calls,
                soft_failure_reason=soft_failure_reason,
                generated_tokens=generated_tokens,
            )
        finally:
            memory_common_module.get_klorb_data_dir = original_get_klorb_data_dir
        if on_complete is not None:
            on_complete(result)
        return result


def run_evaluation(
    cases: list[EvalCase], *, model: str, provider: ApiProvider,
    on_case_start: Callable[[str], None] | None = None,
    on_case_complete: Callable[[CaseResult], None] | None = None,
    trace: bool = False,
) -> list[CaseResult]:
    """Run every case in `cases` in sequence and return their `CaseResult`s in order.

    `on_case_start`/`on_case_complete`, if given, are forwarded to each `run_case()` call as
    `on_start`/`on_complete`; `trace` is forwarded as-is.
    """
    return [
        run_case(
            case, model=model, provider=provider, on_start=on_case_start, on_complete=on_case_complete,
            trace=trace)
        for case in cases
    ]


def _encoding_for_model(model: str) -> tiktoken.Encoding:
    """Best-effort `tiktoken` encoding for `model`: tries `model` as-is, then with any
    OpenRouter `provider/` prefix (e.g. `"openai/"`) stripped, falling back to `o200k_base`
    (GPT-4o's encoding) if neither is recognized. Exact only for OpenAI models; for any other
    model proxied through OpenRouter (Anthropic, Meta, etc. all use their own, unrelated
    tokenizers) this is a reference approximation, not that model's real token count.
    """
    for candidate in (model, model.rsplit("/", 1)[-1]):
        try:
            return tiktoken.encoding_for_model(candidate)
        except KeyError:
            continue
    return tiktoken.get_encoding("o200k_base")


def tool_token_counts(*, model: str) -> dict[str, int]:
    """Token count of every discovered tool's full function-calling definition (the same
    `{"type": "function", "function": {"name", "description", "parameters"}}` dict
    `ToolRegistry.tool_definitions()` builds, JSON-encoded) under `model`'s tokenizer — the
    actual per-turn prompt cost of offering that tool, not just its description text alone.

    This is independent of any particular `EvalCase`: the tool package's tools and their
    schemas never vary by case, so it's computed once per eval run (see `run_evals.main()`)
    rather than per case. `SessionConfig`'s default `workspace` (cwd) is never touched —
    `name()`/`description()`/`parameters()` do no I/O — so no real workspace is needed here.
    """
    tool_registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig(), package=tools_package)
    encoding = _encoding_for_model(model)
    return {
        definition["function"]["name"]: len(encoding.encode(json.dumps(definition)))
        for definition in tool_registry.tool_definitions()
    }
