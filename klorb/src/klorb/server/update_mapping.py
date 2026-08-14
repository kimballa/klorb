# © Copyright 2026 Aaron Kimball
"""Pure functions mapping klorb tool-call events (`klorb.session.events.ToolCallStartedEvent`/
`ToolCallEvent`) onto ACP `session/update` tool-call notifications -- see
docs/specs/klorb-server.md's tool-call update mapping section. Kept free of I/O beyond the
read-only path canonicalization every klorb file tool already performs, so `TurnBridge` (and a
test) can call these directly against a `ToolRegistry` and a workspace root, with no live
`Session`/ACP connection required.

Every function here is total: no klorb event may raise a mapping failure out to the caller. A
per-field failure (a tool's `summary()`/`detail_view()`/`diff_preview()` override raising, or an
unresolvable location) degrades to a simpler rendering (a default summary/detail string, or no
location/diff content) rather than propagating, with the reason logged at `debug` level.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Literal, cast

import acp
from acp.schema import (
    AllowedOutcome,
    DeniedOutcome,
    FileEditToolCallContent,
    PermissionOption,
    PermissionOptionKind,
    SessionMode,
    SessionModeState,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolCallStatus,
    ToolCallUpdate,
    ToolKind,
)
from pydantic import BaseModel, ValidationError

from klorb.message import Message, ToolCallRequest
from klorb.models.registry import ModelRegistry
from klorb.permissions.command_grant import compute_command_grant_patterns
from klorb.permissions.directory_access import canonicalize_dir
from klorb.permissions.resource import CommandResource, PermissionResource
from klorb.permissions.risk_classifier import ItemRiskAssessment
from klorb.session import Session, SessionConfig
from klorb.session.constants import PermissionFramework, ThinkingEffort
from klorb.session.events import (
    AskUserQuestionsAnswer,
    AskUserQuestionsItemContext,
    EscalatePrivilegesContext,
    EscalatePrivilegesDecision,
    PermissionAskContext,
    PermissionDecision,
    ToolCallEvent,
    ToolCallStartedEvent,
)
from klorb.tools.ask.common import QuestionOption, format_answer
from klorb.tools.exceptions import NoSuchToolException
from klorb.tools.registry import ToolRegistry
from klorb.tools.tool import (
    DiffPreview,
    Tool,
    default_tool_call_detail,
    default_tool_call_summary,
    truncate_lines,
)
from klorb.tools.util.diff_lines import DiffHunk

logger = logging.getLogger(__name__)

TOOL_KIND_MAP: dict[str, ToolKind] = {
    "ReadFile": "read",
    "ReadMemory": "read",
    "ReadScratchpad": "read",
    "ReadSkillFile": "read",
    "EditFile": "edit",
    "ReplaceAll": "edit",
    "CreateFile": "edit",
    "EditMemory": "edit",
    "CreateMemory": "edit",
    "EditScratchpad": "edit",
    "Grep": "search",
    "FindFile": "search",
    "ListDir": "search",
    "SearchMemories": "search",
    "SearchScratchpad": "search",
    "SearchSkills": "search",
    "ListMemories": "search",
    "Bash": "execute",
    "WebFetch": "fetch",
    "TodoList": "think",
    "TodoNext": "think",
    "TodoCreate": "think",
    "TodoUpdate": "think",
    "ActivateSkill": "think",
    "ForgetMemory": "delete",
    "AskUserQuestions": "other",
    "EscalatePrivileges": "other",
    "CreateSubagent": "other",
    "WaitForSubagent": "other",
    "MessageSubagent": "other",
}
"""Every tool name `ToolRegistry.discover_tools()` can produce today, mapped to its ACP
`ToolKind` -- see `docs/specs/klorb-server.md`. A name this dict doesn't cover falls back to
`"other"` at lookup time (see `_tool_kind`), the same as an unrecognized future tool would;
`test_update_mapping.py` parametrizes over every currently-discovered tool name so a new tool
added without an entry here fails loudly instead of silently becoming `"other"`."""

TOOL_LOCATION_ARG: dict[str, str] = {
    "ReadFile": "filename",
    "EditFile": "filename",
    "CreateFile": "filename",
    "Grep": "path",
    "FindFile": "dirname",
    "ListDir": "dirname",
}
"""Tool name to the arg key naming the filesystem path a call's ACP `locations` should point at.
A tool not in this dict emits no `locations` at all -- either it has no path-shaped argument, or
(`EditScratchpad`) its subject isn't a model-nameable path in the first place."""


def _instantiate_tool(name: str, tool_registry: ToolRegistry | None) -> Tool | None:
    """Return a fresh instance of the named tool from `tool_registry`, or `None` if the
    registry doesn't have one (no registry at all, or the name isn't registered) -- the same
    fallback shape `klorb.tui.mixins.rendering.RenderingMixin._render_tool_call_summary` uses."""
    if tool_registry is None:
        return None
    try:
        return tool_registry.instantiate_tool(name)
    except NoSuchToolException:
        return None


def _tool_title(name: str, args: dict[str, Any], tool_registry: ToolRegistry | None) -> str:
    """The tool's pre-execution summary line, for a `tool_call` update's `title`: the same
    string `RunningToolCallStatic` shows in the TUI, via `Tool.summary(args)` called with no
    result/error."""
    tool = _instantiate_tool(name, tool_registry)
    if tool is None:
        return default_tool_call_summary(name, args, None)
    try:
        return tool.summary(args)
    except Exception:
        logger.debug("Tool %r summary() raised; falling back to the default summary", name, exc_info=True)
        return default_tool_call_summary(name, args, None)


def _resolve_location_path(value: str, workspace_root: Path) -> str | None:
    try:
        return str(canonicalize_dir(Path(value), workspace_root))
    except Exception:
        logger.debug(
            "Failed to resolve tool-call location %r against workspace root %s",
            value, workspace_root, exc_info=True)
        return None


def _tool_locations(
    name: str, args: dict[str, Any], workspace_root: Path,
) -> list[ToolCallLocation] | None:
    """Return the `[{path, line}]` ACP `locations` list for one call, or `None` if this tool
    (or this particular call's arguments) name no filesystem path -- see `TOOL_LOCATION_ARG`.
    `ReadFile`'s `start_line` arg additionally sets `line` on the one location it reports."""
    arg_key = TOOL_LOCATION_ARG.get(name)
    if arg_key is None:
        return None
    value = args.get(arg_key)
    if not isinstance(value, str):
        return None
    path = _resolve_location_path(value, workspace_root)
    if path is None:
        return None
    line = args.get("start_line") if name == "ReadFile" else None
    if isinstance(line, int):
        return [ToolCallLocation(path=path, line=line)]
    return [ToolCallLocation(path=path)]


def _bash_meta_start(args: dict[str, Any]) -> dict[str, str] | None:
    """Return `_meta.klorb.bash` for a just-started `Bash` call, or `None` for other tools."""
    command = args.get("command")
    if not isinstance(command, str):
        return None
    intent = args.get("intent")
    meta: dict[str, str] = {"command": command}
    if isinstance(intent, str) and intent:
        meta["intent"] = intent
    return meta


def tool_call_started_update(
    event: ToolCallStartedEvent, tool_registry: ToolRegistry | None, workspace_root: Path,
) -> ToolCallStart:
    """Map a just-started tool call onto an ACP `tool_call` (`session/update`) notification:
    `status="in_progress"` unconditionally -- klorb fires `on_tool_call_started` immediately
    before `apply()` runs, so there's no separate `"pending"` phase worth reporting.
    `_meta.klorb.toolName` always carries `event.name` verbatim, so a client can tell apart
    tools that share one `ToolKind` bucket (e.g. `CreateSubagent`/`WaitForSubagent`/
    `MessageSubagent`/`AskUserQuestions` all map to `"other"`)."""
    title = _tool_title(event.name, event.args, tool_registry)
    kind = TOOL_KIND_MAP.get(event.name, "other")
    locations = _tool_locations(event.name, event.args, workspace_root)
    bash_meta = _bash_meta_start(event.args)
    result = acp.start_tool_call(
        event.call_id, title, kind=kind, status="in_progress", locations=locations,
        raw_input=event.args)
    klorb_meta: dict[str, Any] = {"toolName": event.name}
    if bash_meta is not None:
        klorb_meta["bash"] = bash_meta
    result.field_meta = {"klorb": klorb_meta}
    return result


def _diff_text(hunks: list[DiffHunk]) -> tuple[str | None, str]:
    """Reassemble `hunks` (a hunk-with-context view, not a whole file) back into an
    old/new text pair for ACP's `diff` content block -- `oldText`/`newText` are therefore an
    approximation of the touched file, not its literal full contents; see
    docs/adrs/00146-persist-diff-hunks-in-edit-result.md for why klorb persists hunks rather than
    whole files in the first place. `old_text` is `None` when every line is an `"add"` (a
    brand-new file/memory/scratchpad has no prior content to show), matching ACP's own
    `oldText: None` convention for new files."""
    old_lines: list[str] = []
    new_lines: list[str] = []
    for hunk in hunks:
        for line in hunk.lines:
            if line.kind in ("context", "del"):
                old_lines.append(line.text)
            if line.kind in ("context", "add"):
                new_lines.append(line.text)
    old_text = "\n".join(old_lines) if old_lines else None
    new_text = "\n".join(new_lines)
    return old_text, new_text


def _diff_path(args: dict[str, Any], result: Any, name: str, workspace_root: Path) -> str:
    """The path an edit/create call's diff content block is `"about"`: `args["filename"]` for
    every edit-family tool that takes one, `result["filename"]` as a fallback (defensive; every
    tool with a `filename` arg also echoes it into `result`), or the tool's own name for
    `EditScratchpad`, whose subject is harness-managed and never a model-nameable path."""
    filename = args.get("filename")
    if isinstance(filename, str):
        return _resolve_location_path(filename, workspace_root) or filename
    if isinstance(result, dict) and isinstance(result.get("filename"), str):
        return _resolve_location_path(result["filename"], workspace_root) or result["filename"]
    return name


def _diff_content(
    tool: Tool, name: str, args: dict[str, Any], result: Any, workspace_root: Path,
) -> list[FileEditToolCallContent] | None:
    try:
        preview: DiffPreview | None = tool.diff_preview(args, result, None)
    except Exception:
        logger.debug("Tool %r diff_preview() raised; falling back to text content", name, exc_info=True)
        return None
    if preview is None:
        return None
    old_text, new_text = _diff_text(preview.hunks)
    path = _diff_path(args, result, name, workspace_root)
    diff_hunks_meta = [hunk.model_dump() for hunk in preview.hunks]
    return [FileEditToolCallContent(
        type="diff", path=path, new_text=new_text, old_text=old_text,
        field_meta={"klorb": {"diffHunks": diff_hunks_meta}})]


def _failure_content(event: ToolCallEvent) -> list[Any]:
    text = event.error
    assert text is not None
    if event.raw_arguments is not None:
        text = f"{text}\n\nRaw arguments:\n{event.raw_arguments}"
    return [acp.tool_content(acp.text_block(text))]


def _success_content(
    event: ToolCallEvent, tool_registry: ToolRegistry | None, workspace_root: Path,
) -> list[Any]:
    tool = _instantiate_tool(event.name, tool_registry)
    if tool is not None:
        diff_content = _diff_content(tool, event.name, event.args, event.result, workspace_root)
        if diff_content is not None:
            return diff_content
        try:
            detail = tool.detail_view(event.args, event.result, None)
        except Exception:
            logger.debug(
                "Tool %r detail_view() raised; falling back to the default detail",
                event.name, exc_info=True)
            detail = default_tool_call_detail(event.name, event.args, event.result, None)
    else:
        detail = default_tool_call_detail(event.name, event.args, event.result, None)
    return [acp.tool_content(acp.text_block(detail))]


def _json_safe_result(result: Any) -> Any:
    try:
        json.dumps(result)
    except TypeError:
        logger.debug(
            "Tool result of type %s is not JSON-serializable; omitting rawOutput",
            type(result).__name__)
        return None
    return result


def _bash_meta_finish(event: ToolCallEvent) -> dict[str, Any] | None:
    """Return `_meta.klorb.bash` for a finished `Bash` call's result, or `None` for other
    tools or when the result is not a dict (e.g. an error before `apply()` returned).
    Re-includes `command`/`intent` (see `_bash_meta_start`) because the webview client treats
    `command` as the required field that marks a `_meta.klorb.bash` payload as present at all."""
    if event.name != "Bash" or not isinstance(event.result, dict):
        return None
    bash_meta = _bash_meta_start(event.args)
    if bash_meta is None:
        return None
    meta: dict[str, Any] = dict(bash_meta)
    if event.error is not None:
        meta["success"] = False
    elif "success" in event.result:
        meta["success"] = bool(event.result["success"])
    if "exit_status" in event.result and event.result["exit_status"] is not None:
        meta["exitStatus"] = event.result["exit_status"]
    if "runtime" in event.result:
        try:
            meta["runtime"] = round(float(event.result["runtime"]), 2)
        except (TypeError, ValueError):
            pass
    for key in ("stdout", "stderr"):
        value = event.result.get(key)
        if isinstance(value, str):
            meta[key] = truncate_lines(value, 20)
    return meta if meta else None


_READFILE_MAX_META_LINES = 8
"""Maximum content lines included in `_meta.klorb.readFile.content` — matches
`ReadFile.detail_view()`'s own truncation so the metadata doesn't carry more
than the content text block already does."""

_READFILE_META_TOOLS = ("ReadFile", "ReadMemory", "ReadScratchpad")
"""Tool names whose finished call reports `_meta.klorb.readFile` -- every tool built on
`klorb.tools.util.ReadFileCore`, so the webview can render each one's result as the same
structured line-numbered card."""


def _readfile_meta_finish(event: ToolCallEvent) -> dict[str, Any] | None:
    """Return `_meta.klorb.readFile` for a finished call to a tool in `_READFILE_META_TOOLS`,
    or `None` for other tools or when the result lacks content. The `content` field carries
    the truncated line-numbered content (same format as `detail_view()`), so the webview can
    render it as a structured line-numbered card instead of parsing JSON. `filename` falls
    back to the tool's own name for `ReadScratchpad`, whose subject isn't a model-nameable
    path in the first place (same reasoning as `_diff_path`'s fallback)."""
    if event.name not in _READFILE_META_TOOLS or not isinstance(event.result, dict):
        return None
    content = event.result.get("content")
    if not isinstance(content, str):
        return None
    filename = event.args.get("filename")
    if not isinstance(filename, str):
        filename = event.result.get("filename", event.name)
    content = truncate_lines(content, _READFILE_MAX_META_LINES)
    return {"filename": filename, "content": content}


def tool_call_finished_update(
    event: ToolCallEvent, tool_registry: ToolRegistry | None, workspace_root: Path,
) -> ToolCallProgress:
    """Map a finished tool call onto an ACP `tool_call_update` (`session/update`) notification.
    `status` is `"failed"` when `event.error` is set, else `"completed"`. On success, an
    edit-family call whose result carries diff hunks (see `Tool.diff_preview()`) reports one
    ACP `diff` content block; every other call (including a failed one) reports one text content
    block -- the tool's `detail_view()` output on success, or the error string (plus
    `raw_arguments`, for a malformed-JSON call that never reached `apply()`) on failure."""
    status: ToolCallStatus = "failed" if event.error is not None else "completed"
    content = (
        _failure_content(event) if event.error is not None
        else _success_content(event, tool_registry, workspace_root))
    bash_meta = _bash_meta_finish(event)
    readfile_meta = _readfile_meta_finish(event)
    result = acp.update_tool_call(
        event.call_id, status=status, content=content,
        raw_output=_json_safe_result(event.result))
    klorb_meta: dict[str, Any] = {}
    if bash_meta is not None:
        klorb_meta["bash"] = bash_meta
    if readfile_meta is not None:
        klorb_meta["readFile"] = readfile_meta
    if klorb_meta:
        result.field_meta = {"klorb": klorb_meta}
    return result


_PERMISSION_OPTION_SPECS: tuple[tuple[str, PermissionOptionKind, str], ...] = (
    ("allow:once", "allow_once", "Allow once"),
    ("deny:once", "reject_once", "Deny"),
    ("allow:session", "allow_always", "Allow for this session"),
    ("deny:session", "reject_always", "Deny for this session"),
)
"""Options a `session/request_permission` for a `PermissionAskContext` always carries -- see
`permission_ask_options`."""

_PERSISTENT_PERMISSION_OPTION_SPECS: tuple[tuple[str, PermissionOptionKind, str], ...] = (
    ("allow:workspace", "allow_always", "Always allow (workspace)"),
    ("allow:homedir", "allow_always", "Always allow (home config)"),
    ("deny:workspace", "reject_always", "Always deny (workspace)"),
    ("deny:homedir", "reject_always", "Always deny (home config)"),
)
"""Additional options offered only when `PermissionResource.is_persistable` is `True` -- a
`StructuralResource` ask has no rule a workspace/homedir grant could be recorded against.
Mirrors the always-offered specs' allow/deny pairing one scope further out -- see
docs/adrs/00069-generalize-grant-writer-for-deny-and-mirror-it-for-commandrules.md for why a
persistent-scope deny is just as real a grant as a persistent-scope allow."""

_ESCALATE_PRIVILEGES_OPTION_SPECS: tuple[tuple[str, PermissionOptionKind, str], ...] = (
    ("allow:once", "allow_once", "Approve for this session"),
    ("deny:once", "reject_once", "Deny"),
)

_VALID_ACTIONS: tuple[str, ...] = ("allow", "deny")
_VALID_SCOPES: tuple[str, ...] = ("once", "session", "workspace", "homedir")


def _permission_option(option_id: str, kind: PermissionOptionKind, name: str) -> PermissionOption:
    """One `PermissionOption`, with `_meta.klorb.scope` carrying `option_id`'s own scope token
    (the part after the `:`) so a client needn't parse ids -- see
    docs/specs/klorb-server.md's permission-ask option registry."""
    scope = option_id.split(":", 1)[1]
    return PermissionOption(
        option_id=option_id, kind=kind, name=name, field_meta={"klorb": {"scope": scope}})


def permission_ask_options(resource: PermissionResource) -> list[PermissionOption]:
    """The `session/request_permission` options for `resource`: always once/deny-once/session/
    deny-session, plus workspace/homedir allow and deny when `resource.is_persistable` -- a
    `StructuralResource` (no persistable rule of its own) only ever offers the always-on four."""
    specs = list(_PERMISSION_OPTION_SPECS)
    if resource.is_persistable:
        specs += _PERSISTENT_PERMISSION_OPTION_SPECS
    return [_permission_option(option_id, kind, name) for option_id, kind, name in specs]


def escalate_privileges_options() -> list[PermissionOption]:
    """The fixed two-option `session/request_permission` grid for an `EscalatePrivilegesContext`
    ask: approve for this session, or deny -- there is no persistent-grant scope for an
    escalation, which always revokes at the end of the session."""
    return [
        _permission_option(option_id, kind, name)
        for option_id, kind, name in _ESCALATE_PRIVILEGES_OPTION_SPECS]


def permission_ask_tool_call_update(call_id: str | None, fallback_title: str) -> ToolCallUpdate:
    """The `toolCall` a `session/request_permission` request links itself to: the most recent
    in-flight call's own id (`call_id`, tracked by `TurnBridge` -- asks are raised from within a
    call's `apply()`, so one is always live in practice), or, defensively, a freshly synthesized
    id titled `fallback_title` when no call is in flight."""
    if call_id is not None:
        return ToolCallUpdate(tool_call_id=call_id)
    logger.debug(
        "No in-flight tool call to link a permission ask to; synthesizing tool_call_id for %r",
        fallback_title)
    return ToolCallUpdate(tool_call_id=str(uuid.uuid4()), title=fallback_title)


def _display_grant_patterns(
    resource: PermissionResource, risk: ItemRiskAssessment | None, session_config: SessionConfig,
) -> list[list[str]] | None:
    """The `commandRules` pattern(s) to show the client as `_meta.klorb.grantPatterns`: the risk
    classifier's own `suggested_pattern` when it offered one, else the same deterministic
    literal-argv patterns `CommandResource.grant_preview()` computes. `None` for any resource
    kind other than `CommandResource` -- only a bash-command ask has a `commandRules` pattern to
    preview at all."""
    if not isinstance(resource, CommandResource):
        return None
    if risk is not None and risk.suggested_pattern:
        return [risk.suggested_pattern]
    return compute_command_grant_patterns(session_config.command_rules, list(resource.argv))


def permission_decision_grant_patterns(
    resource: PermissionResource, risk: ItemRiskAssessment | None,
) -> list[list[str]] | None:
    """The pattern to thread through as `PermissionDecision.grant_patterns` -- unlike
    `_display_grant_patterns`, this is `None` whenever the risk classifier didn't suggest a
    pattern, so a persistent grant falls back to `apply_command_permission_grant`'s own
    deterministic computation rather than persisting a preview-only fallback pattern that was
    never actually vetted by the classifier -- mirrors `klorb.tui.mixins.interactions.
    InteractionsMixin._confirm_permission_ask`."""
    if isinstance(resource, CommandResource) and risk is not None and risk.suggested_pattern:
        return [risk.suggested_pattern]
    return None


def permission_ask_meta(
    ctx: PermissionAskContext, risk: ItemRiskAssessment | None, item_index: int, item_total: int,
    session_config: SessionConfig,
) -> dict[str, Any]:
    """The `_meta.klorb` payload for a `session/request_permission` request built from `ctx`:
    always `resourceDescription` and `headerKind` (the same "Run command"/`resource.
    header_kind()` noun phrase `PermissionAskPanel.header_text()` uses); for a `BashTool` ask
    (`ctx.bash_context` set), additionally the full/per-item command text, this item's position
    within its sibling batch, a grant-pattern preview, and the risk classifier's score and
    rationale (`risk`, or `None` if classification is disabled, not a bash ask, or the classifier
    failed -- see `klorb.permissions.risk_classifier.resolve_item_risk_assessment`); `originSessionId`
    is included whenever `ctx.origin_session_id` is set (a subagent's ask, forwarded through its
    creator's own turn -- see `klorb.agents.policy.build_subagent_turn_handlers`), so a client
    tracking multiple sessions (the VSCode subagents panel) can gate showing this ask on that
    session being selected, mirroring the TUI's own `SubagentsPanelMixin._await_session_selected`."""
    header_kind = "Run command" if ctx.bash_context is not None else ctx.resource.header_kind()
    meta: dict[str, Any] = {
        "resourceDescription": ctx.resource_description, "headerKind": header_kind}
    if ctx.bash_context is not None:
        meta["commandText"] = ctx.bash_context.command_text
        meta["itemCommandText"] = ctx.bash_context.item_command_text
        meta["itemIndex"] = item_index
        meta["itemTotal"] = item_total
        grant_patterns = _display_grant_patterns(ctx.resource, risk, session_config)
        if grant_patterns is not None:
            meta["grantPatterns"] = grant_patterns
        if risk is not None:
            meta["riskLevel"] = risk.risk_score
            meta["riskRationale"] = risk.rationale
    if ctx.origin_session_id is not None:
        meta["originSessionId"] = ctx.origin_session_id
    return meta


def escalate_privileges_meta(ctx: EscalatePrivilegesContext) -> dict[str, Any]:
    """The `_meta.klorb` payload for an `EscalatePrivilegesContext` ask's `session/
    request_permission` request: `escalation.scope`/`escalation.description`/`escalation.reason`,
    so the client can render this as its own distinct (e.g. red-border) flow rather than an
    ordinary permission grid. `originSessionId` is included when set -- see `permission_ask_meta`'s
    own doc comment."""
    meta: dict[str, Any] = {
        "escalation": {
            "scope": ctx.scope, "description": ctx.description, "reason": ctx.reason}}
    if ctx.origin_session_id is not None:
        meta["originSessionId"] = ctx.origin_session_id
    return meta


def _split_option_id(option_id: str) -> tuple[
    Literal["allow", "deny"], Literal["once", "session", "workspace", "homedir"],
]:
    action, _, scope = option_id.partition(":")
    if action not in _VALID_ACTIONS or scope not in _VALID_SCOPES:
        raise ValueError(f"Unrecognized permission option id: {option_id!r}")
    return cast(Literal["allow", "deny"], action), cast(
        Literal["once", "session", "workspace", "homedir"], scope)


def _other_text(outcome: AllowedOutcome) -> str | None:
    """The user's free-text redirect, if the client's response carried one: a non-empty
    `_meta.klorb.otherText` string on a `selected` outcome, regardless of which option id was
    actually selected alongside it -- see docs/specs/klorb-server.md."""
    if not outcome.field_meta:
        return None
    klorb_meta = outcome.field_meta.get("klorb")
    if not isinstance(klorb_meta, dict):
        return None
    candidate = klorb_meta.get("otherText")
    return candidate if isinstance(candidate, str) and candidate else None


def permission_decision_from_outcome(
    outcome: AllowedOutcome | DeniedOutcome, grant_patterns: list[list[str]] | None,
) -> PermissionDecision:
    """Map a `RequestPermissionResponse.outcome` back onto a `PermissionDecision`: a `cancelled`
    outcome (the client dismissed the request without picking an option) is
    `action="deny", scope="once"`; a `selected` outcome whose `_meta.klorb.otherText` is a
    non-empty string is the free-text redirect (`action="deny", scope="once", other_text=...`),
    regardless of the option id chosen; otherwise the option id's own `<action>:<scope>` encodes
    the decision directly. `grant_patterns` (from `permission_decision_grant_patterns`) is
    threaded through unconditionally, matching `klorb.tui.mixins.interactions.InteractionsMixin.
    _confirm_permission_ask`'s own unconditional threading -- it's only ever consulted downstream
    for a persistent-scope `"allow"`."""
    if outcome.outcome == "cancelled":
        return PermissionDecision(action="deny", scope="once")
    other_text = _other_text(outcome)
    if other_text is not None:
        return PermissionDecision(action="deny", scope="once", other_text=other_text)
    action, scope = _split_option_id(outcome.option_id)
    return PermissionDecision(action=action, scope=scope, grant_patterns=grant_patterns)


def escalate_privileges_decision_from_outcome(
    outcome: AllowedOutcome | DeniedOutcome,
) -> EscalatePrivilegesDecision:
    """Map a `RequestPermissionResponse.outcome` back onto an `EscalatePrivilegesDecision`:
    approved only for a `selected` outcome whose option id is `"allow:once"` (`escalate_privileges_
    options`'s "Approve for this session" option); a `cancelled` outcome, or any other selected
    option id, is a denial."""
    if outcome.outcome == "cancelled":
        return EscalatePrivilegesDecision(approved=False)
    return EscalatePrivilegesDecision(approved=outcome.option_id == "allow:once")


def ask_user_questions_ext_params(
    ctx: AskUserQuestionsItemContext, session_id: str,
) -> dict[str, Any]:
    """The `_klorb/askUserQuestions` ext request params for one question of an
    `AskUserQuestionsItemContext` batch: `{sessionId, header, question, options: [{label,
    description?}], index, total}` -- `index`/`total` verbatim from `ctx`, since klorb asks
    serially, one request per question, exactly as the TUI panel is driven. `originSessionId` is
    included whenever `ctx.origin_session_id` is set -- see `permission_ask_meta`'s own doc
    comment for why."""
    params: dict[str, Any] = {
        "sessionId": session_id,
        "header": ctx.header,
        "question": ctx.question,
        "options": [_ask_user_questions_option(option) for option in ctx.options],
        "index": ctx.index,
        "total": ctx.total,
    }
    if ctx.origin_session_id is not None:
        params["originSessionId"] = ctx.origin_session_id
    return params


def _ask_user_questions_option(option: QuestionOption) -> dict[str, Any]:
    return (
        {"label": option.label, "description": option.description} if option.description
        else {"label": option.label})


def ask_user_questions_answer_from_result(
    ctx: AskUserQuestionsItemContext, result: dict[str, Any],
) -> AskUserQuestionsAnswer:
    """Map a `_klorb/askUserQuestions` result (`{selectedOptionIndex: int} | {otherText: str} |
    {cancelled: true}`) back onto an `AskUserQuestionsAnswer`. The final answer string is
    formatted here (`klorb.tools.ask.common.format_answer`), not by the client, the same
    server-owns-the-one-formatting-rule invariant `permission_decision_from_outcome` follows for
    a permission ask's free-text redirect. A `selectedOptionIndex` outside `ctx.options`'s range,
    or a result naming none of the three recognized keys, raises `ValueError`, propagating as a
    turn failure -- a compliant client only ever echoes back a shape this request itself allows."""
    if result.get("cancelled") is True:
        return AskUserQuestionsAnswer(cancelled=True)
    if "otherText" in result:
        other_text = result["otherText"]
        if not isinstance(other_text, str):
            raise ValueError(f"_klorb/askUserQuestions otherText must be a string: {other_text!r}")
        return AskUserQuestionsAnswer(answer=format_answer(None, other_text))
    selected_index = result.get("selectedOptionIndex")
    if not isinstance(selected_index, int) or isinstance(selected_index, bool) or not (
        0 <= selected_index < len(ctx.options)
    ):
        raise ValueError(
            f"_klorb/askUserQuestions selectedOptionIndex out of range: {selected_index!r}")
    return AskUserQuestionsAnswer(answer=format_answer(ctx.options[selected_index], None))


SESSION_MODES: list[SessionMode] = [
    SessionMode(id="ask", name="Ask before acting"),
    SessionMode(id="auto", name="Auto-approve"),
    SessionMode(id="deny", name="Deny tool asks"),
]
"""Every `PermissionFramework` literal, mapped to the fixed ACP `SessionMode` labels
`klorb.server.klorb_agent` advertises on `session/new` and `session/set_mode` -- see
docs/specs/klorb-server.md's "Session modes" section."""


def session_mode_state(permission_framework: PermissionFramework) -> SessionModeState:
    """Map a session's current `permission_framework` onto ACP's session-modes surface: the
    fixed `SESSION_MODES` list, with `currentModeId` set from `permission_framework`."""
    return SessionModeState(available_modes=SESSION_MODES, current_mode_id=permission_framework)


def session_config_json(session: Session, model_registry: ModelRegistry) -> dict[str, Any]:
    """Build the `_klorb/getSessionConfig`/`_klorb/setSessionConfig` result payload: the
    session's current model and thinking settings, plus every model `model_registry` knows
    about -- see docs/specs/klorb-server.md's "Model and thinking session config" section. The
    pinned ACP SDK (0.7.x) has no generic select/boolean config-option surface, and no notion
    of `thinking.enabled`/`thinking.effort` at all (`session/set_model` exists but is marked
    unstable and only covers `model`), so both ride this one ext-method JSON shape uniformly
    rather than splitting model onto the native surface and thinking onto an ext method."""
    active_model = session.active_model()
    return {
        "model": {
            "current": session.config.model,
            "available": [
                {"id": model.name(), "name": model.name()}
                for model in sorted(model_registry.models(), key=lambda model: model.name())
            ],
        },
        "thinking": {
            "enabled": session.config.thinking_enabled,
            "effort": session.config.thinking_effort,
        },
        "activeModelVision": bool(active_model.capabilities().get("vision")) if active_model else False,
    }


class SessionConfigThinkingUpdate(BaseModel):
    """The `thinking` sub-object of a `_klorb/setSessionConfig` request's params -- see
    `SessionConfigUpdate`. A field left unset (`None`) is left unchanged by the caller."""

    enabled: bool | None = None
    effort: ThinkingEffort | None = None


class SessionConfigUpdate(BaseModel):
    """A validated `_klorb/setSessionConfig` request's params -- see
    `parse_session_config_update`. A field left unset (`None`) is left unchanged by the
    caller."""

    model: str | None = None
    thinking: SessionConfigThinkingUpdate | None = None


def parse_session_config_update(params: dict[str, Any]) -> SessionConfigUpdate:
    """Validate a `_klorb/setSessionConfig` request's params against `SessionConfigUpdate`,
    raising `acp.RequestError.invalid_params` (e.g. for an unrecognized `thinking.effort`
    string) instead of letting a `pydantic.ValidationError` escape as an unhandled
    exception."""
    try:
        return SessionConfigUpdate.model_validate(params)
    except ValidationError as exc:
        raise acp.RequestError.invalid_params({"reason": str(exc)}) from exc


def _replay_bash_meta(
    call: ToolCallRequest, args: dict[str, Any],
    parsed: dict[str, Any] | None, failed: bool,
) -> dict[str, Any] | None:
    """Build `bashMeta` for a replayed tool-call entry from the restored call/response pair."""
    if call.name != "Bash":
        return None
    meta: dict[str, Any] = {}
    command = args.get("command")
    if isinstance(command, str):
        meta["command"] = command
    intent = args.get("intent")
    if isinstance(intent, str) and intent:
        meta["intent"] = intent
    if parsed is not None and "response_body" in parsed:
        body = parsed["response_body"]
        if isinstance(body, dict):
            if failed:
                meta["success"] = False
            elif "success" in body:
                meta["success"] = bool(body["success"])
            if body.get("exit_status") is not None:
                meta["exitStatus"] = body["exit_status"]
            if "runtime" in body:
                try:
                    meta["runtime"] = round(float(body["runtime"]), 2)
                except (TypeError, ValueError):
                    pass
    return meta if meta else None


def _replay_readfile_meta(
    call: ToolCallRequest, args: dict[str, Any],
    parsed: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build `readFileMeta` for a replayed tool-call entry from the restored call/response pair."""
    if call.name not in _READFILE_META_TOOLS or parsed is None:
        return None
    body = parsed.get("response_body")
    if not isinstance(body, dict):
        return None
    content = body.get("content")
    if not isinstance(content, str):
        return None
    filename = args.get("filename")
    if not isinstance(filename, str):
        filename = body.get("filename", call.name)
    content = truncate_lines(content, _READFILE_MAX_META_LINES)
    return {"filename": filename, "content": content}


def _replay_tool_call_entry(
    call: ToolCallRequest, response: Message | None,
    tool_registry: ToolRegistry | None, workspace_root: Path,
) -> dict[str, Any]:
    """Build one `_klorb/sessionReplay` `toolCall`-kind entry from a restored `role="tool_use"`
    message's request and its matching `role="tool_response"` message (if any) -- best-effort
    reversal of `Session._run_tool_calls`'s persisted encoding, the same reasoning
    `klorb.tui.mixins.rendering.RenderingMixin._render_restored_tool_call` applies for the TUI's
    own history-scroll restore: a `response.content` that's a JSON `klorb.tools.
    response_envelope.ToolResponseEnvelope` (`is_error`/`error_message`/`response_body`) is
    decoded structurally; a pre-envelope save (`"Error: {message}"` or a bare string) falls back
    to prefix-matching, best-effort. Locations/toolKind/title reuse the same helpers a live
    `tool_call` update uses, so a replayed call looks the same as it would have live."""
    try:
        args = json.loads(call.arguments) if call.arguments else {}
        if not isinstance(args, dict):
            args = {}
    except json.JSONDecodeError:
        args = {}

    content_text: str | None = None
    failed = False
    parsed: dict[str, Any] | None = None
    if response is not None:
        try:
            parsed_raw = json.loads(response.content)
        except json.JSONDecodeError:
            parsed_raw = None
        parsed = parsed_raw if isinstance(parsed_raw, dict) else None
        if parsed is not None and "is_error" in parsed:
            failed = bool(parsed["is_error"])
            content_text = (
                parsed.get("error_message") if failed else parsed.get("response_body")
            )
            if content_text is not None and not isinstance(content_text, str):
                content_text = json.dumps(content_text, ensure_ascii=False)
        elif response.content.startswith("Error: "):
            failed = True
            content_text = response.content[len("Error: "):]
        else:
            content_text = response.content

    locations = _tool_locations(call.name, args, workspace_root)
    entry: dict[str, Any] = {
        "kind": "toolCall",
        "callId": call.id,
        "status": "failed" if failed else "completed",
        "title": _tool_title(call.name, args, tool_registry),
        "toolKind": TOOL_KIND_MAP.get(call.name, "other"),
        "locations": [loc.model_dump(mode="json") for loc in locations] if locations else [],
        "contentText": content_text,
        "expanded": False,
    }
    bash_meta = _replay_bash_meta(call, args, parsed if isinstance(parsed, dict) else None, failed)
    if bash_meta is not None:
        entry["bashMeta"] = bash_meta
    readfile_meta = _replay_readfile_meta(call, args, parsed if isinstance(parsed, dict) else None)
    if readfile_meta is not None:
        entry["readFileMeta"] = readfile_meta
    return entry


def _replay_image_meta(message: Message) -> list[dict[str, Any]]:
    """Build the `AttachedImageMeta`-shaped dicts (`shared/webviewMessages.ts`) for `message`'s
    `image_url` fragments, if any -- metadata only, no bytes: a `_klorb/sessionReplay` restore
    doesn't resend an already-persisted image just to redraw a thumbnail (see docs/specs/
    session-persistence.md), so the webview renders a paper-clip placeholder captioned with
    whatever of `source_filename`/`original_width`/`original_height` survived persistence (see
    `klorb.message.MessageFragment`). A key is omitted rather than sent as `null` for an unknown
    field, matching every other optional field this dict's TS counterpart expects absent, not
    `null`, when unset.
    """
    if message.fragments is None:
        return []
    images: list[dict[str, Any]] = []
    for fragment in message.fragments:
        if fragment.type != "image_url":
            continue
        meta: dict[str, Any] = {}
        if fragment.source_filename is not None:
            meta["name"] = fragment.source_filename
        if fragment.original_width is not None:
            meta["width"] = fragment.original_width
        if fragment.original_height is not None:
            meta["height"] = fragment.original_height
        images.append(meta)
    return images


def _readable_reasoning_text(entry: dict[str, Any]) -> str | None:
    text = entry.get("text")
    if isinstance(text, str):
        return text
    summary = entry.get("summary")
    return summary if isinstance(summary, str) else None


def _resolve_thinking_text(content: str, reasoning_details: list[dict[str, Any]] | None) -> str:
    """Reconstruct a `"thinking"` message's replay text from `reasoning_details` when `content`
    itself is empty -- `content` and `reasoning_details` are populated by two independent
    provider streams that aren't guaranteed to stay in sync (see `klorb.message.Message.
    reasoning_details`), so a `content`-only replay can render an empty `<Thinking>` block even
    though real reasoning text arrived. Duplicates `klorb.tui.formatting.
    resolve_thinking_body_text`'s logic rather than importing it: that module pulls in
    `textual`/`rich` at import time, which this server module must not depend on."""
    if content.strip():
        return content
    if not reasoning_details:
        return content
    readable = list(filter(None, map(_readable_reasoning_text, reasoning_details)))
    return "\n\n".join(readable) if readable else content


def build_session_replay(
    session: Session, tool_registry: ToolRegistry | None, workspace_root: Path,
) -> list[dict[str, Any]]:
    """Build the `entries` payload for a `_klorb/sessionReplay` ext notification (see
    `KlorbAcpAgent.load_session`) or a `_klorb/subagentTranscript` ext method result (see
    `KlorbAcpAgent._ext_subagent_transcript`): one `HistoryEntry`-shaped dict (matching the
    webview's own `shared/webviewMessages.ts`-adjacent `HistoryEntry` shape) per restored
    message, in order. `role="system"`/`"tool_defs"` bookkeeping messages are skipped, matching
    how they're never rendered live either; a `role="tool_response"` is folded into its matching
    `role="tool_use"` entry (see `_replay_tool_call_entry`) rather than appearing on its own. A
    `role="tool_use"` message's own `content` (commentary alongside the tool calls it requested --
    e.g. the model's final answer, when that answer arrives in the same round as its last tool
    calls) is emitted as its own `"response"`-kind entry ahead of that message's tool calls, and a
    `role="thinking"` message's text is resolved via `_resolve_thinking_text` -- both mirror the
    same two gaps `klorb.tui.formatting.resolve_thinking_body_text` and the TUI's own
    `tool_use`-content handling close for the TUI's restored-history/subagent-transcript render
    paths (see docs/specs/subagents.md's "Subagents panel (TUI)" section).
    """
    entries: list[dict[str, Any]] = []
    responses_by_call_id = {
        message.tool_call_id: message for message in session.messages
        if message.role == "tool_response" and message.tool_call_id is not None
    }
    text_kind_by_role = {"user": "prompt", "assistant": "response", "thinking": "thinking"}
    for message in session.messages:
        if message.role in text_kind_by_role:
            text = (
                _resolve_thinking_text(message.content, message.reasoning_details)
                if message.role == "thinking" else message.content)
            entry: dict[str, Any] = {
                "kind": text_kind_by_role[message.role], "text": text,
                "streaming": False,
            }
            images = _replay_image_meta(message) if message.role == "user" else []
            if images:
                entry["images"] = images
            entries.append(entry)
        elif message.role == "tool_use":
            if message.content.strip():
                entries.append({"kind": "response", "text": message.content, "streaming": False})
            for call in message.tool_calls or []:
                response = responses_by_call_id.get(call.id)
                entries.append(_replay_tool_call_entry(call, response, tool_registry, workspace_root))
    return entries
