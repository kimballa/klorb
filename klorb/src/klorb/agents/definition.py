# © Copyright 2026 Aaron Kimball
"""Pydantic models validating one `agents.json` entry's shape, once `klorb.schema_envelope.
parse_versioned_json` has stripped the file's `schema` envelope. See
docs/specs/subagents.md."""

from typing import Any

from pydantic import BaseModel, Field

from klorb.hooks.config import EventConfig, HookConfig
from klorb.hooks.merge import parse_event_dict, parse_session_scoped_hook_dict

AGENT_SCHEMA_NAME = "klorb-agents"
AGENT_SCHEMA_VERSION = "1.0.0"


class AgentRestrictions(BaseModel):
    """Filters that narrow which tools, skills, and subagent roles a subagent inherits from
    its parent."""

    tools: list[str] | None = None
    """Tool names to keep, intersected against the parent's own effective tool set. `None`
    inherits every tool the parent has; `tool_categories`/`enforce_readonly_tools`, if also
    set, narrow this further rather than replacing it."""
    tool_categories: list[str] | None = None
    """`Tool.category()` values to keep."""
    skills: list[str] | None = None
    """Fully-qualified skill names to keep, intersected against
    the parent's own effective skill set. `None` inherits every skill the parent has."""
    subagent_roles: list[str] | None = None
    """Role names this subagent may itself pass to `CreateSubagent`, intersected against the
    roles *this* subagent was granted."""
    enforce_readonly_tools: bool = False
    """When `True`, clamp the tool set (after the `tools`/`tool_categories` filters above) to
    only tools reporting `Tool.is_read_only() == True`, plus anything in the `"SCRATCHPAD"`
    category regardless of `is_read_only()`."""


class AgentCapabilities(BaseModel):
    """Capabilities gating how a subagent role may use tools it otherwise has access to."""

    accepts_tasks: bool = False
    """Whether a session running as this role may hold a chainlink issue as its own current
    tracked task."""
    assigns_tasks: bool = False
    """Whether a session running as this role may `TodoCreate` an issue with `assign_to` naming
    a *different* agent's id."""
    see_group_tasks: bool = False
    """Whether a session running as this role may `TodoList` with `scope="group"` to see every
    issue in the group, not just its own."""
    send_messages: bool = False
    """Whether a session running as this role may `SendMessage` another agent in the group.
    Any role may receive a message and use `GetMessages` regardless of this flag."""


class AgentDefinition(BaseModel):
    """One `agents.json` entry: the capability policy for a named subagent role."""

    name: str
    """The subagent role name, e.g. `"explorer"`."""
    default_model: str
    """Model used for this role's subagent unless the parent's `CreateSubagent` call
    overrides it."""
    restrict_to: AgentRestrictions = Field(default_factory=AgentRestrictions)
    allow_subagents: bool = False
    """Whether a subagent running as this role may itself call `CreateSubagent`."""
    agent_capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    hooks: dict[str, Any] = Field(default_factory=dict)
    """Raw `{hookName: [handler, ...]}` entries this role grants to every subagent created as
    it, the same shape a skill's `metadata.klorb.hooks` frontmatter carries."""
    events: dict[str, Any] = Field(default_factory=dict)
    """Raw `{eventName: [entry, ...]}` entries this role grants, the same shape a skill's
    `metadata.klorb.events` frontmatter carries."""


def agent_hook_configs(definition: AgentDefinition) -> dict[str, list[HookConfig]]:
    """`definition`'s own `hooks` entries, parsed into `HookConfig` lists."""
    return parse_session_scoped_hook_dict(
        definition.hooks, source_label=f"agents.json role {definition.name!r} hooks")


def agent_event_configs(definition: AgentDefinition) -> dict[str, list[EventConfig]]:
    """`definition`'s own `events` entries, parsed into the right `EventConfig` subclass per
    `klorb.hooks.config.EVENT_CONFIG_MODELS`."""
    return parse_event_dict(
        definition.events, source_label=f"agents.json role {definition.name!r} events")
