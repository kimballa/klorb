# © Copyright 2026 Aaron Kimball
"""Subagent-creation policy: the depth/`allow_subagents`/concurrency rejection checks
`CreateSubagent`/`MessageSubagent` run before starting a subagent's turn, the tools/skills/
subagent-roles intersection that produces a child's `SessionConfig` and tool registry, and the
background-thread plumbing that runs a subagent's turn asynchronously with respect to its
creator. See docs/specs/subagents.md's "Security model" and "Subagent session model" sections.

Every check and computation here runs against the *calling* session's own live,
already-narrowed effective sets (its `tool_registry`, its `config.skill_rules`, its
`effective_subagent_roles`) -- never a fresh `agents.json` lookup by role name alone, per the
"no widening across more than one hop" invariant described there. `compute_root_session_grants`
is the one deliberate exception: a root (top-level, user-facing) session has no real parent
session to narrow from, so it computes its own grants by intersecting its role's `agents.json`
entry directly against the unrestricted universal catalog -- the same intersection a subagent
gets against its parent, just with "parent" being "everything" for this one case.
"""

import logging
import threading
from dataclasses import dataclass

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
from klorb.tools.exceptions import ToolCallError
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.skill.catalog import SkillCatalogRegistry, resolve_session_skill_catalog_registry
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


@dataclass
class SubagentPlan:
    """Everything `CreateSubagentTool.apply()` needs to actually construct a child `Session`,
    already validated and computed by `plan_subagent_creation()`."""

    role_definition: AgentDefinition
    session_config: SessionConfig
    tool_classes: "dict[str, type[Tool]]"
    effective_subagent_roles: frozenset[str]


def _resolve_role_definition(parent: Session, role: str) -> AgentDefinition:
    """Look up `role`'s `AgentDefinition`, raising `ToolCallError` if it names no role
    `agents.json` defines, or a role outside `parent`'s own effective `subagent_roles` set.
    `parent.effective_subagent_roles` is always a concrete, already-computed set -- for a
    subagent, from its own creation (`compute_child_subagent_roles`); for a root session, from
    `compute_root_session_grants`."""
    allowed_roles = parent.effective_subagent_roles
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


def check_concurrency_limits(context: ToolSetupContext, session: Session) -> None:
    """Raise `ToolCallError` (category `"transient"`) if starting one more subagent turn on
    `session` -- via `CreateSubagent`, or `MessageSubagent` resuming a dormant one back into
    `"running"` -- would exceed `tools.subagents.maxConcurrentPerParent` or `maxActiveTotal`.
    `"transient"` (not `"validation"`): both limits bound how many subagent turns may run *at
    once*, not the request itself, so retrying once an existing subagent finishes (`
    WaitForSubagent`) is the correct recovery, not fixing the call's arguments. See
    `klorb.agents.runtime.SubagentTracker.running_count` for what "running" means here."""
    max_concurrent = context.process_config.subagents_max_concurrent_per_parent
    if session.subagent_tracker.running_count() >= max_concurrent:
        raise ToolCallError(
            f"This agent already has {max_concurrent} subagent(s) running -- the most allowed "
            f"at once. Call WaitForSubagent so one finishes, then try again.",
            category="transient")
    max_active_total = context.process_config.subagents_max_active_total
    if total_active_subagents(session) >= max_active_total:
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
    check_concurrency_limits(context, parent)
    return role_definition


def _child_tool_classes(
    parent_tool_registry: ToolRegistry, restrict_to: AgentRestrictions, allow_subagents: bool,
) -> "dict[str, type[Tool]]":
    """Compute a child's effective tool class map: `compute_child_tool_set` intersected against
    `parent_tool_registry` (never a fresh full discovery -- an already-narrowed registry must not
    hand a child back anything it doesn't itself have), then `SUBAGENT_MGMT_TOOL_NAMES` stripped
    out unless `allow_subagents` is `True` for the child's own role. Used both for a subagent (the
    "parent" is its creator's live `tool_registry`) and for a root session (the "parent" is the
    unrestricted universal catalog -- see `compute_root_session_grants`). See
    docs/specs/subagents.md."""
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
    `parent_config.skill_rules` already denies -- a snapshot taken at creation time, matching how
    a child's own `tool_registry` is likewise a one-time snapshot. Used both for a subagent (the
    "parent" is its creator's own skill catalog) and for a root session (the "parent" is every
    trusted skill we scan -- see `compute_root_session_grants`)."""
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
    `Session` and its `ToolRegistry`, raising `ToolCallError` on the first check that fails
    (category `"validation"` for the depth/role/allow_subagents checks, `"transient"` for the
    concurrency checks -- see `check_concurrency_limits`) -- no session is constructed until
    every check passes.

    `allowed_tools`/`allowed_skills`, if given, override the role's own `restrict_to.tools`/
    `restrict_to.skills` for this one call (still intersected against the parent's own
    effective sets, never widening it). See docs/specs/subagents.md.
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
    tool_classes = _child_tool_classes(parent.tool_registry, restrict_to, role_definition.allow_subagents)
    skill_rules = _child_skill_rules(
        resolve_session_skill_catalog_registry(context), parent.config, restrict_to)
    subagent_roles = compute_child_subagent_roles(parent.effective_subagent_roles, restrict_to)

    child_config = parent.config.model_copy(deep=True)
    child_config.permission_framework_state = parent.config.permission_framework_state
    child_config.role_name = role
    child_config.skill_rules = skill_rules

    return SubagentPlan(
        role_definition=role_definition, session_config=child_config,
        tool_classes=tool_classes, effective_subagent_roles=frozenset(subagent_roles))


@dataclass
class RootSessionGrants:
    """Everything a root (top-level, user-facing) `Session(...)` construction site needs to build
    its own tool registry, skill rules, and effective subagent-role set -- computed by
    `compute_root_session_grants`."""

    tool_registry: ToolRegistry
    skill_rules: SkillRules
    effective_subagent_roles: frozenset[str]


def compute_root_session_grants(
    process_config: ProcessConfig, session_config: SessionConfig, role_name: str,
) -> RootSessionGrants:
    """Compute the grants a root session running as `role_name` starts with: `role_name`'s own
    `agents.json` `restrict_to`, intersected -- via the same `_child_tool_classes`/
    `_child_skill_rules`/`compute_child_subagent_roles` a subagent's own creation uses -- against
    the unrestricted universal catalog (every tool `ToolRegistry.discover_tools` finds, every
    skill on disk, every role `agents.json` defines).

    A root session has no real parent `Session` to narrow from, so this is the one place that
    intersection runs against "everything" rather than a live parent's already-narrowed sets --
    but it is still the *same* intersection a subagent of this role would get, computed once here
    rather than left for `plan_subagent_creation` to patch around later. A role with no
    `agents.json` entry gets an unrestricted `AgentRestrictions()` (today's behavior for an
    undefined role) but no subagent-launch ability, per `AgentDefinition.allow_subagents`'s
    default.
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
        claude_skills_compat=process_config.compatibility_claude_skills)
    skill_rules = _child_skill_rules(skill_catalog_registry, session_config, restrict_to)
    subagent_roles = compute_child_subagent_roles(frozenset(get_agent_registry().names()), restrict_to)

    return RootSessionGrants(
        tool_registry=ToolRegistry(process_config, session_config, tool_classes),
        skill_rules=skill_rules,
        effective_subagent_roles=frozenset(subagent_roles))


def _subagent_ask_tag(address: str, role: str) -> str:
    """The `"[subagent 1.1 (explorer)]"`-style prefix every ask-style context's human-readable
    text field is stamped with, so a permission/question/escalation panel routed through a
    creating session's own interactive UI (see `build_subagent_turn_handlers`) makes clear which
    subagent the ask is actually for."""
    return f"[subagent {address} ({role})]"


def _stamp_subagent_origin(origin_session_id: str | None, handle: SubagentHandle) -> str:
    """The `origin_session_id` an ask-style context should carry once it's forwarded through
    `handle`'s own `on_*` closure: `handle.session.id` if this is the first (innermost) hop to
    tag it, else whatever an earlier, deeper hop already stamped -- so a multi-level forward
    (grandchild -> child -> root) keeps citing the leaf subagent that actually raised the ask,
    not whichever ancestor's closure last forwarded it. See
    `klorb.session.events.PermissionAskContext.origin_session_id`."""
    return origin_session_id or handle.session.id


def build_subagent_turn_handlers(
    parent: Session, handle: SubagentHandle, cancel_event: threading.Event,
) -> TurnEventHandlers:
    """Build the `TurnEventHandlers` a subagent's background-thread turn runs with: no
    streaming/UI-progress callbacks (nothing renders a subagent's turn directly today), but
    every ask-style callback (`on_permission_ask`/`on_ask_user_questions`/
    `on_escalate_privileges`) forwarded to whichever callback `parent`'s own turn is *currently*
    using (captured once, here, from `parent.current_turn_handlers()`), tagged with the
    subagent's address/role and stamped with its `origin_session_id` -- so an interactive ask a
    subagent's turn raises still reaches the user, through today's single-session UI, and a UI
    that gates showing a panel on which session is currently selected (see
    `klorb.tui.mixins.subagents_panel.SubagentsPanelMixin`) knows which session to wait for. See
    docs/specs/subagents.md's "Security model" and "Subagents panel" sections.

    `cancel_event` is this subagent's own, dedicated cancellation signal (not shared with
    `parent`'s current turn) -- set by `klorb.agents.runtime.cascade_close_subagents` on
    shutdown, and by a per-subagent Stop action (`KeyActionsMixin._interrupt_running_activity`).
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
    tool-call rounds before its final plain-text reply (only the *last* message can be plain
    `"assistant"` -- `Session._dispatch_turn` loops only while a reply's role is `"tool_use"` --
    but an earlier `"tool_use"`-role reply can itself carry non-empty commentary text alongside
    the tool calls it requested); using all of it, not just the final message, keeps that
    commentary from being silently discarded."""
    parts = [m.content for m in messages if m.role in ("assistant", "tool_use") and m.content.strip()]
    return "\n\n".join(parts)


def _run_subagent_turn(child: Session, message: str, handlers: TurnEventHandlers) -> str:
    """Run one turn of `child`'s conversation to completion, returning the text to deliver to
    the creating session: every assistant-authored message produced during the turn,
    concatenated in order (see `_assistant_authored_text`), a placeholder if none of it said
    anything, the same concatenation plus an abort note if `handlers.cancel_event` fired
    mid-stream, or a failure note if the turn raised. Never raises -- this is the background
    thread's own top-level call, and an unhandled exception here would silently strand the
    subagent as "running" forever."""
    start_index = len(child.messages)
    try:
        child.send_turn(message, callbacks=handlers, resolve_mentions=False)
        output = _assistant_authored_text(child.messages[start_index:])
        return output if output else "The subagent completed its work without saying anything."
    except ResponseAborted:
        output = _assistant_authored_text(child.messages[start_index:])
        return f"{output}\n\n{SUBAGENT_ABORTED_MARKER}".strip()
    except Exception as exc:
        logger.exception("Subagent %s turn failed", child.id)
        return f"(Subagent turn failed: {exc})"


def dispatch_subagent_turn(
    parent: Session, child: Session, role: str, title: str, message: str,
) -> SubagentHandle:
    """Register `child` with `parent.subagent_tracker` and start a daemon thread running one
    turn of its conversation, returning the `SubagentHandle` immediately -- the subagent runs
    asynchronously with respect to `parent`, which is expected to keep going about its own turn.
    `parent.subagent_tracker.mark_finished()` is called once the background turn ends, however
    it ends, queuing its output for delivery via `WaitForSubagent` or the standing
    `SUBAGENT_INTERJECTION_SUBJECT` interjection.
    """
    cancel_event = threading.Event()

    def worker() -> None:
        output = _run_subagent_turn(child, message, handlers)
        parent.subagent_tracker.mark_finished(child.id, output)

    thread = threading.Thread(target=worker, name=f"subagent-{child.id}", daemon=True)
    handle = SubagentHandle(session=child, thread=thread,
                            cancel_event=cancel_event, role=role, title=title)
    # `handlers` (referenced by `worker`, above) is resolved via closure late-binding -- safe
    # since `worker` never runs until `thread.start()`, after this assignment.
    handlers = build_subagent_turn_handlers(parent, handle, cancel_event)
    parent.subagent_tracker.register(handle)
    thread.start()
    return handle
