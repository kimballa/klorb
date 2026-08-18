# © Copyright 2026 Aaron Kimball
"""Pydantic models validating one `agents.json` entry's shape, once `klorb.schema_envelope.
parse_versioned_json` has stripped the file's `schema` envelope. See
docs/specs/subagents.md."""

from pydantic import BaseModel, Field

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
