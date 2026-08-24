# © Copyright 2026 Aaron Kimball
"""`CreateSubagentTool`: launches a new subagent session running a specialist role, with its
own turn dispatched asynchronously on a background thread. See docs/specs/subagents.md."""

import logging
from typing import Any

from pydantic import BaseModel, Field

from klorb.agents.policy import dispatch_subagent_turn, plan_subagent_creation
from klorb.models.placeholders import resolve_placeholder_model
from klorb.session import Session
from klorb.tools.exceptions import ToolCallError
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.common import SUBAGENT_TOOL_CATEGORY
from klorb.tools.tasks.common import ALL_LABEL, TASK_TOOL_NAMES, ChainlinkClient, agent_label
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class CreateSubagentParameters(BaseModel):
    role: str = Field(description=(
        "The pre-defined subagent role to launch, e.g. \"explorer\"."))
    session_title: str = Field(description=(
        "A short, human-readable title for this subagent session, shown to the user (not to "
        "the subagent)."))
    initial_message: str = Field(description=(
        "The instructions/question you're giving the subagent -- its user message."))
    model: str | None = Field(default=None, description=(
        "Override the default model for the role. Accepts a literal model id or one of the "
        "\"klorb-default/fast\", \"klorb-default/normal\", \"klorb-default/heavy\", "
        "\"klorb-default/current\" placeholders. Omit to use the role's default."))
    allowed_tools: list[str] | None = Field(default=None, description=(
        "Override the role's default tool list for this subagent. This does not "
        "grant any tools you do not already have access to yourself. Omit for default."))
    allowed_skills: list[str] | None = Field(default=None, description=(
        "Override the role's default skill list for this one subagent. This does not "
        "grant any skills you do not already have access to yourself. Omit for default."))
    max_output_tokens: int | None = Field(default=None, description=(
        "Output token budget (includes thinking tokens) to give this subagent. Omit for no "
        "particular limit."))
    starting_task_id: int | None = Field(default=None, description=(
        "Pre-claim this chainlink task for the new subagent instead of requiring it to call "
        "TodoCreate/TodoNext itself. The task's summary is incorporated into the subagent's "
        "first user prompt. If the task carries the \"all\" (unclaimed) label, it is claimed "
        "for the new subagent before the subagent starts."))


class CreateSubagentTool(Tool):
    """Validates and launches a subagent session.

    Verifies the caller has permission and the role is valid, computes the skill/tool intersection
    between the role and the current agent's own sets, then starts the subagent's first turn
    asynchronously and returns immediately.
    """

    def name(self) -> str:
        return "CreateSubagent"

    def category(self) -> str:
        return SUBAGENT_TOOL_CATEGORY

    def is_read_only(self) -> bool:
        """`True`: this tool doesn't mutate any file or environment state directly."""
        return True

    def description(self) -> str:
        return (
            "Launch a new subagent session running a specialist role to perform a "
            "task for you. Use to do two things at once, or to keep research tasks from "
            "overfilling your context. Call with role=\"\" to list available roles.\n"

            "Returns the subagent's id. You will receive the agent's output later, when it is done. "
            "If you need to wait for the subagent's response, use WaitForSubagent to be notified "
            "when the subagent completes.\n"

            "Do not expose the returned id to the user; it's only useful for your own "
            "SendMessage calls. "
        )

    def parameters(self) -> type[BaseModel]:
        return CreateSubagentParameters

    def apply(self, args: dict[str, Any]) -> Any:
        context: ToolSetupContext = self.context
        assert context.session is not None
        starting_task_id: int | None = args.get("starting_task_id")
        plan = plan_subagent_creation(
            context, args.get("role", ""), args.get("allowed_tools"), args.get("allowed_skills"))
        model = args.get("model") or plan.role_definition.default_model
        try:
            model = resolve_placeholder_model(
                model,
                fast=context.process_config.default_model_fast,
                normal=context.process_config.default_model_normal,
                heavy=context.process_config.default_model_heavy,
                current=context.session.config.model,
            )
        except ValueError as exc:
            raise ToolCallError(str(exc), category="validation") from exc
        child_config = plan.session_config
        child_config.model = model
        child_tool_registry = ToolRegistry(context.process_config, child_config, plan.tool_classes)
        child = Session(
            child_config,
            provider=context.session.provider,
            model_registry=context.session.model_registry,
            process_config=context.process_config,
            session_name=args["session_title"],
            tool_registry=child_tool_registry,
            scratchpad_path=str(context.session.scratchpad.path),
            parent=context.session,
            root_id=context.session.root_id,
            effective_subagent_roles=plan.effective_subagent_roles,
            max_output_tokens=args.get("max_output_tokens"),
        )
        if plan.role_events:
            # A subagent never fires its own onSessionStart, so the role's own event grant
            # needs its watcher/scheduler started explicitly here.
            child._start_event_watchers_for(plan.role_events)
        logger.debug(
            "Created subagent %s (role=%s, model=%s) under %s",
            child.id, args["role"], model, context.session.id)
        if not TASK_TOOL_NAMES.isdisjoint(plan.tool_classes.keys()):
            context.session.ensure_chainlink_client()
        initial_message = args["initial_message"]
        if starting_task_id is not None:
            initial_message = self._claim_and_annotate_starting_task(
                context, child, starting_task_id, initial_message)
        dispatch_subagent_turn(
            context.process_config, context.session, child, args["role"], args["session_title"],
            initial_message)
        return {
            "subagent_id": child.id,
            "note": (
                """
                The subagent is now running. If it finishes before you do, its output will be
                delivered to you automatically, the next time you're available to receive it. If you
                have no more work to do before you need its answer, call WaitForSubagent to wait for
                it. Once it has finished, use SendMessage to send it a follow-up if needed. Do
                not expose this id to the user -- it has no meaning to them.
                """
            ),
        }

    def _claim_and_annotate_starting_task(
        self, context: ToolSetupContext, child: Session, task_id: int, initial_message: str,
    ) -> str:
        """Look up `task_id` in chainlink, claim it for `child` if it's unclaimed, and prepend
        the task's summary to `initial_message`. Raises `ToolCallError` (category `"validation"`)
        if the task doesn't exist, is closed, or is already claimed by a different agent."""
        client = ChainlinkClient(context)
        try:
            issue = client.show_issue(task_id)
        except Exception as exc:
            raise ToolCallError(
                f"starting_task_id {task_id} could not be resolved: {exc}",
                category="validation") from exc
        if issue.get("status") != "open":
            raise ToolCallError(
                f"starting_task_id {task_id} is not open (status={issue.get('status')!r}).",
                category="validation")
        labels: list[str] = issue.get("labels", [])
        child_label = agent_label(child.id)
        if ALL_LABEL in labels:
            if not client.remove_label(task_id, ALL_LABEL):
                raise ToolCallError(
                    f"starting_task_id {task_id} was claimed by another agent before the "
                    "subagent could start.",
                    category="validation")
            client.add_label(task_id, child_label)
            logger.debug(
                "Claimed task #%d for subagent %s (removed %r, added %r).",
                task_id, child.id, ALL_LABEL, child_label)
        elif child_label not in labels:
            # Task is already owned by some other agent.
            other_agents = list(filter(lambda lb: lb.startswith("agent:"), labels))
            owner = other_agents[0] if other_agents else "another agent"
            raise ToolCallError(
                f"starting_task_id {task_id} is already claimed by {owner}.",
                category="validation")
        title = issue.get("title", "(untitled)")
        description = issue.get("description")
        task_summary = f'Your assigned task is #{task_id}: "{title}".'
        if description:
            task_summary += f"\n\n{description}"
        return f"{task_summary}\n\n{initial_message}"

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        title = args.get("session_title", "?")
        role = args.get("role", "?")
        base = f"Create subagent ({role}): {title}"
        return base if error is None else f"{base} failed: {error}"
