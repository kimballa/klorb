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
from pathlib import Path
from typing import Any

import acp
from acp.schema import (
    FileEditToolCallContent,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
    ToolCallStatus,
    ToolKind,
)

from klorb.permissions.directory_access import canonicalize_dir
from klorb.session.events import ToolCallEvent, ToolCallStartedEvent
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
