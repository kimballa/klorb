# © Copyright 2026 Aaron Kimball
"""Pydantic models validating one `agents.json` entry's shape, once `klorb.schema_envelope.
parse_versioned_json` has stripped the file's `schema` envelope. See
docs/plans/ready/021-subagents.md's "Setup and config files / vars" section."""

from pydantic import BaseModel, Field

AGENT_SCHEMA_NAME = "klorb-agents"
AGENT_SCHEMA_VERSION = "1.0.0"


class AgentRestrictions(BaseModel):
    """Filters that narrow which tools, skills, and subagent roles a subagent inherits from
    its parent -- never widens: every field here can only remove from what the parent
    already has effectively available (see `klorb.agents.intersection`). `None` (the
    default) means "inherit everything the parent has"; an explicit empty list means
    "inherit nothing," a deliberately different value from `None` -- see
    docs/plans/ready/021-subagents.md's "Tool and skill limits" section.
    """

    tools: list[str] | None = None
    """Tool names to keep, intersected against the parent's own effective tool set. `None`
    inherits every tool the parent has; `tool_categories`/`enforce_readonly_tools`, if also
    set, narrow this further rather than replacing it."""
    tool_categories: list[str] | None = None
    """`Tool.category()` values to keep (e.g. `"FILES"`, `"MEMORY"`) -- lets a role admit
    whole categories, including tools that don't exist yet, without naming each one. `None`
    means no category filter is applied."""
    skills: list[str] | None = None
    """Fully-qualified skill names (e.g. `"internal:someSkill"`) to keep, intersected against
    the parent's own effective skill set. `None` inherits every skill the parent has."""
    subagent_roles: list[str] | None = None
    """Role names this subagent may itself pass to `CreateSubagent`, intersected against the
    roles *this* subagent was granted -- never a fresh lookup of what the role nominally
    allows, so a grandparent's narrowing can't be recovered two hops down. `None` inherits
    every role the parent could itself launch."""
    enforce_readonly_tools: bool = False
    """When `True`, clamp the tool set (after the `tools`/`tool_categories` filters above) to
    only tools reporting `Tool.is_read_only() == True`, plus anything in the `"SCRATCHPAD"`
    category regardless of `is_read_only()` -- the scratchpad is harness-managed shared
    workspace state, not the user's files, so a read-only role can still collaborate through
    it. See docs/plans/ready/021-subagents.md's "Tool and skill limits" section."""


class AgentDefinition(BaseModel):
    """One `agents.json` entry: the capability policy for a named subagent role."""

    name: str
    """The subagent role name, e.g. `"explorer"` -- matches a `SessionConfig.role_name` a
    `CreateSubagent` call can request, and the `system_prompts.d/roles/<name>/` directory
    its system prompt is resolved from (see `klorb.role.get_role`)."""
    default_model: str
    """Model used for this role's subagent unless the parent's `CreateSubagent` call
    overrides it."""
    restrict_to: AgentRestrictions = Field(default_factory=AgentRestrictions)
    allow_subagents: bool = False
    """Whether a subagent running as this role may itself call `CreateSubagent` -- enforced by
    simply omitting `CreateSubagent`/`WaitForSubagent`/`MessageSubagent` from the tool catalog
    offered to a role for which this is `False`, rather than offering the tools and failing at
    call time."""
