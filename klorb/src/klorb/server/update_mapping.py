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
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolCallStatus,
    ToolCallUpdate,
    ToolKind,
)

from klorb.permissions.command_grant import compute_command_grant_patterns
from klorb.permissions.directory_access import canonicalize_dir
from klorb.permissions.resource import CommandResource, PermissionResource
from klorb.permissions.risk_classifier import ItemRiskAssessment
from klorb.session import SessionConfig
from klorb.session.events import (
    EscalatePrivilegesContext,
    EscalatePrivilegesDecision,
    PermissionAskContext,
    PermissionDecision,
    ToolCallEvent,
    ToolCallStartedEvent,
)
from klorb.tools.exceptions import NoSuchToolException
from klorb.tools.registry import ToolRegistry
from klorb.tools.tool import DiffPreview, Tool, default_tool_call_detail, default_tool_call_summary
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


def tool_call_started_update(
    event: ToolCallStartedEvent, tool_registry: ToolRegistry | None, workspace_root: Path,
) -> ToolCallStart:
    """Map a just-started tool call onto an ACP `tool_call` (`session/update`) notification:
    `status="in_progress"` unconditionally -- klorb fires `on_tool_call_started` immediately
    before `apply()` runs, so there's no separate `"pending"` phase worth reporting."""
    title = _tool_title(event.name, event.args, tool_registry)
    kind = TOOL_KIND_MAP.get(event.name, "other")
    locations = _tool_locations(event.name, event.args, workspace_root)
    return acp.start_tool_call(
        event.call_id, title, kind=kind, status="in_progress", locations=locations,
        raw_input=event.args)


def _diff_text(hunks: list[DiffHunk]) -> tuple[str | None, str]:
    """Reassemble `hunks` (a hunk-with-context view, not a whole file) back into an
    old/new text pair for ACP's `diff` content block -- `oldText`/`newText` are therefore an
    approximation of the touched file, not its literal full contents; see
    docs/adrs/persist-diff-hunks-in-edit-result.md for why klorb persists hunks rather than
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
    return acp.update_tool_call(
        event.call_id, status=status, content=content,
        raw_output=_json_safe_result(event.result))


_PERMISSION_OPTION_SPECS: tuple[tuple[str, PermissionOptionKind, str], ...] = (
    ("allow:once", "allow_once", "Allow once"),
    ("deny:once", "reject_once", "Deny"),
    ("allow:session", "allow_always", "Allow for this session"),
)
"""Options a `session/request_permission` for a `PermissionAskContext` always carries -- see
`permission_ask_options`."""

_PERSISTENT_PERMISSION_OPTION_SPECS: tuple[tuple[str, PermissionOptionKind, str], ...] = (
    ("allow:workspace", "allow_always", "Always allow (workspace)"),
    ("allow:homedir", "allow_always", "Always allow (home config)"),
)
"""Additional options offered only when `PermissionResource.is_persistable` is `True` -- a
`StructuralResource` ask has no rule a workspace/homedir grant could be recorded against."""

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
    """The `session/request_permission` options for `resource`: always once/deny/session, plus
    workspace/homedir when `resource.is_persistable` -- a `StructuralResource` (no persistable
    rule of its own) only ever offers once/deny/session."""
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
    always `resourceDescription`; for a `BashTool` ask (`ctx.bash_context` set), additionally the
    full/per-item command text, this item's position within its sibling batch, a grant-pattern
    preview, and the risk classifier's score (`risk`, or `None` if classification is disabled,
    not a bash ask, or the classifier failed -- see
    `klorb.permissions.risk_classifier.resolve_item_risk_assessment`)."""
    meta: dict[str, Any] = {"resourceDescription": ctx.resource_description}
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
    return meta


def escalate_privileges_meta(ctx: EscalatePrivilegesContext) -> dict[str, Any]:
    """The `_meta.klorb` payload for an `EscalatePrivilegesContext` ask's `session/
    request_permission` request: `escalation.scope`/`escalation.description`, so the client can
    render this as its own distinct (e.g. red-border) flow rather than an ordinary permission
    grid."""
    return {"escalation": {"scope": ctx.scope, "description": ctx.description}}


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
