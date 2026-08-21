# © Copyright 2026 Aaron Kimball
"""Subagent-creation policy: the depth/`allow_subagents`/concurrency rejection checks
`CreateSubagent`/`MessageSubagent` run before starting a subagent's turn, the tools/skills/
subagent-roles intersection that produces a child's `SessionConfig` and tool registry, and the
background-thread plumbing that runs a subagent's turn asynchronously with respect to its
creator. See docs/specs/subagents.md."""

import logging
import threading
from dataclasses import dataclass
from typing import Literal

from klorb.agents.definition import AgentDefinition, AgentRestrictions
from klorb.agents.intersection import (
    ToolMetadata,
    compute_child_skill_set,
    compute_child_subagent_roles,
    compute_child_tool_set,
)
from klorb.agents.registry import get_agent_registry
from klorb.agents.runtime import (
    SUBAGENT_ABORTED_MARKER,
    SUBAGENT_MGMT_TOOL_NAMES,
    SubagentHandle,
    total_active_subagents,
)
from klorb.api_provider import ResponseAborted
from klorb.hooks.config import filter_heritable_events, filter_heritable_hooks
from klorb.message import Message
from klorb.permissions.skill_access import SkillRules, format_fqsn, parse_fqsn
from klorb.process_config import ProcessConfig
from klorb.session import (
    AskUserQuestionsAnswer,
    AskUserQuestionsItemContext,
    EscalatePrivilegesContext,
    EscalatePrivilegesDecision,
    PermissionAskContext,
    PermissionDecision,
    Session,
    SessionConfig,
    TurnEventHandlers,
)
from klorb.session.events import QueuedMessage
from klorb.tools.exceptions import ToolCallError
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.skill.catalog import SkillCatalogRegistry, resolve_session_skill_catalog_registry
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


@dataclass
class SubagentPlan:
    """Everything `CreateSubagentTool.apply()` needs to actually construct a child `Session`."""

    role_definition: AgentDefinition
    session_config: SessionConfig
    tool_classes: "dict[str, type[Tool]]"
    effective_subagent_roles: frozenset[str]


def _resolve_role_definition(parent: Session, role: str) -> AgentDefinition:
    """Look up `role`'s `AgentDefinition`, raising `ToolCallError` if it names no role
    `agents.json` defines, or a role outside `parent`'s own effective `subagent_roles` set.
    `parent.effective_subagent_roles` is always a concrete, already-computed set."""
    allowed_roles = parent.effective_subagent_roles
    if not allowed_roles:
        raise ToolCallError(
            f"The {parent.config.role_name!r} role may not create subagents.",
            category="validation")
    if not role:
        raise ToolCallError(
            f"You must specify one of the following subagent roles to launch: role="
            f"{sorted(allowed_roles)}.", category="validation")
    if role not in allowed_roles:
        raise ToolCallError(
            f"Role {role!r} is not among the subagent roles this agent may launch: "
            f"{sorted(allowed_roles)}.", category="validation")
    definition = get_agent_registry().get(role)
    if definition is None:
        raise ToolCallError(f"No such subagent role: {role!r}", category="validation")
    return definition


def _exceeds_per_parent_limit(process_config: ProcessConfig, session: Session) -> bool:
    return session.subagent_tracker.running_count() >= process_config.subagents_max_concurrent_per_parent


def _exceeds_total_limit(process_config: ProcessConfig, session: Session) -> bool:
    return total_active_subagents(session) >= process_config.subagents_max_active_total


def concurrency_limits_exceeded(process_config: ProcessConfig, session: Session) -> bool:
    """Whether starting one more subagent turn on `session` right now would exceed
    `tools.subagents.maxConcurrentPerParent` or `maxActiveTotal`."""
    return _exceeds_per_parent_limit(process_config, session) or _exceeds_total_limit(process_config, session)


def check_concurrency_limits(process_config: ProcessConfig, session: Session) -> None:
    """Raise `ToolCallError` (category `"transient"`) if starting one more subagent turn on
    `session` would exceed
    `tools.subagents.maxConcurrentPerParent` or `maxActiveTotal`."""
    max_concurrent = process_config.subagents_max_concurrent_per_parent
    if _exceeds_per_parent_limit(process_config, session):
        raise ToolCallError(
            f"This agent already has {max_concurrent} subagent(s) running -- the most allowed "
            f"at once. Call WaitForSubagent so one finishes, then try again.",
            category="transient")
    max_active_total = process_config.subagents_max_active_total
    if _exceeds_total_limit(process_config, session):
        raise ToolCallError(
            f"This session tree already has {max_active_total} subagent(s) running in total -- "
            f"the most allowed at once. Call WaitForSubagent so one finishes, then try again.",
            category="transient")


def _check_creation_limits(context: ToolSetupContext, parent: Session, role: str) -> AgentDefinition:
    """Run every upfront rejection check `CreateSubagent` must pass before any session is
    constructed, raising `ToolCallError` on the first one that fails. Returns the requested
    role's `AgentDefinition` on success."""
    max_depth = context.process_config.subagents_max_depth
    if parent.depth + 1 > max_depth:
        raise ToolCallError(
            f"Creating a subagent here would exceed the maximum subagent nesting depth "
            f"({max_depth}).", category="validation")
    caller_definition = get_agent_registry().get(parent.config.role_name)
    if caller_definition is None or not caller_definition.allow_subagents:
        raise ToolCallError(
            f"The {parent.config.role_name!r} role may not create subagents.",
            category="validation")
    role_definition = _resolve_role_definition(parent, role)
    check_concurrency_limits(context.process_config, parent)
    return role_definition


def _child_tool_classes(
    parent_tool_registry: ToolRegistry, restrict_to: AgentRestrictions, allow_subagents: bool,
) -> "dict[str, type[Tool]]":
    """Compute a child's effective tool class map: `compute_child_tool_set` intersected against
    `parent_tool_registry`, then `SUBAGENT_MGMT_TOOL_NAMES` stripped
    out unless `allow_subagents` is `True` for the child's own role."""
    parent_classes = parent_tool_registry.tool_classes()
    parent_metadata: dict[str, ToolMetadata] = {}
    for tool in parent_tool_registry.tools():
        parent_metadata[tool.name()] = ToolMetadata(
            category=tool.category(), is_read_only=tool.is_read_only())
    effective_names = compute_child_tool_set(parent_metadata, restrict_to)
    if not allow_subagents:
        effective_names -= SUBAGENT_MGMT_TOOL_NAMES
    return {name: cls for name, cls in parent_classes.items() if name in effective_names}


def _child_skill_rules(
    skill_catalog_registry: SkillCatalogRegistry, parent_config: SessionConfig,
    restrict_to: AgentRestrictions,
) -> SkillRules:
    """Compute a child's effective `SkillRules`: every fully-qualified skill name currently
    discoverable in `skill_catalog_registry` (already `ensure()`d by the caller against the
    parent's own workspace/trust settings) is intersected via `compute_child_skill_set`; whichever
    names fall out of that intersection are added to the child's `deny` list on top of whatever
    `parent_config.skill_rules` already denies."""
    parent_skill_ids = {
        format_fqsn((skill.namespace, skill.name))
        for skill in skill_catalog_registry.canonical().discoverable(parent_config.skill_rules)
    }
    effective_names = compute_child_skill_set(parent_skill_ids, restrict_to)
    newly_denied = [parse_fqsn(fqsn) for fqsn in parent_skill_ids if fqsn not in effective_names]
    return SkillRules(
        deny=[*parent_config.skill_rules.deny, *newly_denied],
        ask=list(parent_config.skill_rules.ask),
        allow=list(parent_config.skill_rules.allow),
    )


def plan_subagent_creation(
    context: ToolSetupContext, role: str,
    allowed_tools: list[str] | None, allowed_skills: list[str] | None,
) -> SubagentPlan:
    """Validate a `CreateSubagent` call and compute everything needed to construct the child
    `Session` and its `ToolRegistry`, raising `ToolCallError` on the first check that fails.

    `allowed_tools`/`allowed_skills`, if given, override the role's own `restrict_to.tools`/
    `restrict_to.skills` for this one call (still intersected against the parent's own
    effective sets, never widening it).
    """
    assert context.session is not None
    parent = context.session
    role_definition = _check_creation_limits(context, parent, role)
    restrict_to = role_definition.restrict_to
    if allowed_tools is not None:
        restrict_to = restrict_to.model_copy(update={"tools": allowed_tools})
    if allowed_skills is not None:
        restrict_to = restrict_to.model_copy(update={"skills": allowed_skills})

    assert parent.tool_registry is not None
    tool_classes = _child_tool_classes(parent.tool_registry, restrict_to,
                                       role_definition.allow_subagents)
    skill_rules = _child_skill_rules(
        resolve_session_skill_catalog_registry(context), parent.config, restrict_to)
    subagent_roles = compute_child_subagent_roles(parent.effective_subagent_roles, restrict_to)

    child_config = parent.config.model_copy(deep=True)
    child_config.permission_framework_state = parent.config.permission_framework_state
    child_config.role_name = role
    child_config.skill_rules = skill_rules
    child_config.hooks = filter_heritable_hooks(child_config.hooks)
    child_config.events = filter_heritable_events(child_config.events)

    return SubagentPlan(
        role_definition=role_definition, session_config=child_config,
        tool_classes=tool_classes, effective_subagent_roles=frozenset(subagent_roles))


@dataclass
class RootSessionGrants:
    """Everything a root (top-level, user-facing) `Session(...)` construction site needs to build
    its own tool registry, skill rules, and effective subagent-role set."""

    tool_registry: ToolRegistry
    skill_rules: SkillRules
    effective_subagent_roles: frozenset[str]


def compute_root_session_grants(
    process_config: ProcessConfig, session_config: SessionConfig, role_name: str,
) -> RootSessionGrants:
    """Compute the grants a root session running as `role_name` starts with: `role_name`'s own
    `agents.json` `restrict_to`, intersected against the unrestricted universal catalog (every
    tool `ToolRegistry.discover_tools` finds, every skill on disk, every role `agents.json`
    defines).

    A root session has no real parent `Session` to narrow from, so this is the one place that
    intersection runs against "everything" rather than a live parent's already-narrowed sets.
    A role with no `agents.json` entry gets an unrestricted `AgentRestrictions()` but no
    subagent-launch ability, per `AgentDefinition.allow_subagents`'s default.
    """
    universe = ToolRegistry.discover_tools(process_config, session_config)
    definition = get_agent_registry().get(role_name)
    restrict_to = definition.restrict_to if definition is not None else AgentRestrictions()
    allow_subagents = definition is not None and definition.allow_subagents

    tool_classes = _child_tool_classes(universe, restrict_to, allow_subagents)
    skill_catalog_registry = SkillCatalogRegistry()
    skill_catalog_registry.ensure(
        workspace_root=session_config.workspace.path,
        workspace_trusted=session_config.workspace.trusted,
        claude_skills_compat=process_config.compatibility_claude_skills,
        skill_rules=session_config.skill_rules)
    skill_rules = _child_skill_rules(skill_catalog_registry, session_config, restrict_to)
    subagent_roles = compute_child_subagent_roles(frozenset(get_agent_registry().names()), restrict_to)

    return RootSessionGrants(
        tool_registry=ToolRegistry(process_config, session_config, tool_classes),
        skill_rules=skill_rules,
        effective_subagent_roles=frozenset(subagent_roles))


def _subagent_ask_tag(address: str, role: str) -> str:
    """The `"[subagent 1.1 (explorer)]"`-style prefix every ask-style context's human-readable
    text field is stamped with, so a permission/question/escalation panel routed through a
    creating session's own interactive UI makes clear which subagent the ask is actually for."""
    return f"[subagent {address} ({role})]"


def _stamp_subagent_origin(origin_session_id: str | None, handle: SubagentHandle) -> str:
    """The `origin_session_id` an ask-style context should carry once it's forwarded through
    `handle`'s own `on_*` closure: `handle.session.id` if this is the first (innermost) hop to
    tag it, else whatever an earlier, deeper hop already stamped."""
    return origin_session_id or handle.session.id


def build_subagent_turn_handlers(
    parent: Session, handle: SubagentHandle, cancel_event: threading.Event,
) -> TurnEventHandlers:
    """Build the `TurnEventHandlers` a subagent's background-thread turn runs with: no
    streaming/UI-progress callbacks (nothing renders a subagent's turn directly today), but
    every ask-style callback (`on_permission_ask`/`on_ask_user_questions`/
    `on_escalate_privileges`) forwarded to whichever callback `parent`'s own turn is *currently*
    using, tagged with the subagent's address/role and stamped with its `origin_session_id`.

    `cancel_event` is this subagent's own, dedicated cancellation signal.
    """
    parent_handlers = parent.current_turn_handlers() or TurnEventHandlers()
    address = handle.session.address()
    role = handle.role
    tag = _subagent_ask_tag(address, role)

    def on_permission_ask(ask_ctx: PermissionAskContext) -> PermissionDecision:
        if parent_handlers.on_permission_ask is None:
            raise ToolCallError(str(ask_ctx.resource_description), category="permission")
        tagged = ask_ctx.model_copy(update={
            "resource_description": f"{tag} {ask_ctx.resource_description}",
            "origin_session_id": _stamp_subagent_origin(ask_ctx.origin_session_id, handle)})
        return parent_handlers.on_permission_ask(tagged)

    def on_ask_user_questions(ask_ctx: AskUserQuestionsItemContext) -> AskUserQuestionsAnswer:
        if parent_handlers.on_ask_user_questions is None:
            return AskUserQuestionsAnswer(cancelled=True)
        tagged = ask_ctx.model_copy(update={
            "header": f"{tag} {ask_ctx.header}",
            "origin_session_id": _stamp_subagent_origin(ask_ctx.origin_session_id, handle)})
        return parent_handlers.on_ask_user_questions(tagged)

    def on_escalate_privileges(ask_ctx: EscalatePrivilegesContext) -> EscalatePrivilegesDecision:
        if parent_handlers.on_escalate_privileges is None:
            return EscalatePrivilegesDecision(approved=False)
        tagged = ask_ctx.model_copy(update={
            "description": f"{tag} {ask_ctx.description}",
            "origin_session_id": _stamp_subagent_origin(ask_ctx.origin_session_id, handle)})
        return parent_handlers.on_escalate_privileges(tagged)

    return TurnEventHandlers(
        cancel_event=cancel_event,
        on_permission_ask=on_permission_ask,
        on_ask_user_questions=on_ask_user_questions,
        on_escalate_privileges=on_escalate_privileges,
    )


def _assistant_authored_text(messages: list[Message]) -> str:
    """Concatenate the `content` of every `role="assistant"`/`"tool_use"` message in `messages`,
    in order, skipping empty ones. A subagent's turn may emit commentary alongside one or more
    tool-call rounds before its final plain-text reply; using all of it, not just the final
    message, keeps that commentary from being silently discarded."""
    parts = [m.content for m in messages if m.role in ("assistant", "tool_use") and m.content.strip()]
    return "\n\n".join(parts)


def _run_subagent_turn(child: Session, message: str, handlers: TurnEventHandlers) -> str:
    """Run `child`'s conversation to completion, returning the text to deliver to the creating
    session: every assistant-authored message produced, concatenated in order, a placeholder if
    none of it said anything, the same concatenation plus an abort note if
    `handlers.cancel_event` fired mid-stream, or a failure note if a turn raised. Never raises.

    Dispatches `onSubagentStart`/`onSubagentTurnEnd` from `child`'s own *parent* around each
    turn, covering every way a subagent's turn is kicked off. A `onSubagentStart` veto
    (`fire_subagent_start_hook` returning `None`) skips the turn entirely, reporting a blocked
    note back to the creating session.

    Loops on an ordinary successful completion: `onSubagentTurnEnd`'s `chat`-handler
    continuation is delivered via `Session._deliver_chained_hook_message` on `child`'s own
    conversation. An abort or exception stops the chain immediately after firing the hook once,
    without attempting to drain further."""
    assert child.parent is not None
    parent = child.parent
    effective_message = parent.fire_subagent_start_hook(child, message)
    if effective_message is None:
        return "(Subagent blocked by onSubagentStart hook policy.)"
    start_index = len(child.messages)
    pending_message: str | None = effective_message
    result = ""
    while pending_message is not None:
        try:
            child.send_turn(pending_message, callbacks=handlers, resolve_mentions=False)
            output = _assistant_authored_text(child.messages[start_index:])
            result = output if output else "The subagent completed its work without saying anything."
        except ResponseAborted:
            output = _assistant_authored_text(child.messages[start_index:])
            result = f"{output}\n\n{SUBAGENT_ABORTED_MARKER}".strip()
            parent.fire_subagent_turn_end_hook(child, result)
            return result
        except Exception as exc:
            logger.exception("Subagent %s turn failed", child.id)
            result = f"(Subagent turn failed: {exc})"
            parent.fire_subagent_turn_end_hook(child, result)
            return result
        parent.fire_subagent_turn_end_hook(child, result)
        pending_message = child.drain_next_turn_text(handlers)
    return result


def dispatch_subagent_turn(
    parent: Session, child: Session, role: str, title: str, message: str,
    *, parent_interested: bool = True,
) -> SubagentHandle:
    """Register `child` with `parent.subagent_tracker` and start a daemon thread running one
    turn of its conversation, returning the `SubagentHandle` immediately."""
    cancel_event = threading.Event()

    def worker() -> None:
        output = _run_subagent_turn(child, message, handlers)
        parent.subagent_tracker.mark_finished(child.id, output)

    thread = threading.Thread(target=worker, name=f"subagent-{child.id}", daemon=True)
    handle = SubagentHandle(session=child, thread=thread, cancel_event=cancel_event, role=role,
                            title=title, parent_interested=parent_interested)
    # `handlers` is resolved via closure late-binding.
    handlers = build_subagent_turn_handlers(parent, handle, cancel_event)
    parent.subagent_tracker.register(handle)
    thread.start()
    return handle


def dispatch_direct_message(
    process_config: ProcessConfig, child: Session, handle: SubagentHandle, message: str,
) -> Literal["queued", "started"]:
    """Send `message` from a human user directly to `child`, an existing subagent anywhere in the
    session tree. If `child`'s turn is already running, enqueues into it without touching
    `handle.parent_interested`: that turn was dispatched by whoever started it (usually the
    parent), and a human steering it mid-turn doesn't change who's expecting the outcome. If
    `child` is dormant, starts a fresh turn: this turn belongs to the human alone, and the parent
    must not have its output rolled into its own context. Returns which of the two happened,
    decided under `child.parent.subagent_tracker.dispatch_guard()` against the tracker's current
    handle for `child` rather than the possibly-stale `handle` argument.

    Raises `ToolCallError` if `child` is dormant and resuming it would exceed
    `tools.subagents.maxConcurrentPerParent`/`maxActiveTotal`.
    """
    assert child.parent is not None
    tracker = child.parent.subagent_tracker
    with tracker.dispatch_guard():
        current = tracker.current_handle(child.id) or handle
        if current.state == "running":
            child.enqueue_queued_message(QueuedMessage(message_text=message))
            return "queued"
        check_concurrency_limits(process_config, child.parent)
        dispatch_subagent_turn(
            child.parent, child, current.role, current.title, message, parent_interested=False)
        return "started"
