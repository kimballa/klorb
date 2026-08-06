# © Copyright 2026 Aaron Kimball
"""`CreateSubagentTool`: launches a new subagent session running a specialist role, with its
own turn dispatched asynchronously on a background thread. See
docs/specs/subagents.md's "CreateSubagent" section.
"""

import logging
from typing import Any

from pydantic import BaseModel, Field

from klorb.agents.policy import dispatch_subagent_turn, plan_subagent_creation
from klorb.session import Session
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.subagents.common import SUBAGENT_TOOL_CATEGORY
from klorb.tools.tasks.common import TASK_TOOL_NAMES
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
        "Override the default model for the role. Omit to use the role's default."))
    allowed_tools: list[str] | None = Field(default=None, description=(
        "Override the role's default tool list for this subagent. This does not "
        "grant any tools you do not already have access to yourself. Omit for default."))
    allowed_skills: list[str] | None = Field(default=None, description=(
        "Override the role's default skill list for this one subagent. This does not "
        "grant any skills you do not already have access to yourself. Omit for default."))
    max_output_tokens: int | None = Field(default=None, description=(
        "Output token budget (includes thinking tokens) to give this subagent. Omit for no "
        "particular limit."))


class CreateSubagentTool(Tool):
    """
    Validates and launches a subagent session.

    Verifies that the agent has permission to launch the requested subagent. Verifies the subagent
    role is valid to launch. Populates a set of skills and tools the subagent is permitted to use,
    by taking the intersection of the approved set for the subagent role and the current agent's own
    skill/tool sets.

    Creates a new background session, starts the subagent's the first turn asynchronously. and
    returns immediately. The caller is expected to keep working and either receive the subagent's
    output opportunistically (a `SystemInterjection`) or call `WaitForSubagent` to wait for it.
    """

    def name(self) -> str:
        return "CreateSubagent"

    def category(self) -> str:
        return SUBAGENT_TOOL_CATEGORY

    def is_read_only(self) -> bool:
        """`True`: this tool doesn't mutate any file or environment state directly -- a
        subagent's actual capabilities are bounded by the tool/skill intersection computed for
        it, not by whether this tool itself is offered under `enforce_readonly_tools`."""
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
            "MessageSubagent calls. "
        )

    def parameters(self) -> type[BaseModel]:
        return CreateSubagentParameters

    def apply(self, args: dict[str, Any]) -> Any:
        context: ToolSetupContext = self.context
        assert context.session is not None
        plan = plan_subagent_creation(
            context, args.get("role", ""), args.get("allowed_tools"), args.get("allowed_skills"))
        model = args.get("model") or plan.role_definition.default_model
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
        logger.debug(
            "Created subagent %s (role=%s, model=%s) under %s",
            child.id, args["role"], model, context.session.id)
        if set(plan.tool_classes) & TASK_TOOL_NAMES:
            # The new subagent's own tool set includes a TASKS tool, so it could plausibly
            # create/track chainlink issues -- ensure the creating session has a ChainlinkClient
            # of its own, so the group's close-time cleanup gets registered even if the creator
            # never calls a Todo* tool itself. See Session.ensure_chainlink_client().
            context.session.ensure_chainlink_client()
        dispatch_subagent_turn(
            context.session, child, args["role"], args["session_title"], args["initial_message"])
        return {
            "subagent_id": child.id,
            "note": (
                """
                The subagent is now running. If it finishes before you do, its output will be
                delivered to you automatically, the next time you're available to receive it. If you
                have no more work to do before you need its answer, call WaitForSubagent to wait for
                it. Once it has finished, use MessageSubagent to send it a follow-up if needed. Do
                not expose this id to the user -- it has no meaning to them.
                """
            ),
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        title = args.get("session_title", "?")
        role = args.get("role", "?")
        base = f"Create subagent ({role}): {title}"
        return base if error is None else f"{base} failed: {error}"
