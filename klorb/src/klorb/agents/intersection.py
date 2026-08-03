# © Copyright 2026 Aaron Kimball
"""Computes a subagent's effective tool/skill/subagent-role sets from its parent's own
effective sets plus an `AgentRestrictions`. The safety-critical core of subagent capability
scoping -- see docs/specs/subagents.md's "Security model" section -- kept as
a standalone, pure function so it can be unit-tested in isolation before any tool-dispatch
code calls it.

Every function here only ever narrows: a child's effective set is always a subset of its
parent's, computed from arguments the caller passes in -- never widened, and never re-derived
from `agents.json` by role name (a caller must always pass the parent's own already-narrowed
live effective sets, not a fresh `AgentRegistry` lookup, or a multi-level subagent chain could
recover privileges an intervening level deliberately stripped).
"""

from collections.abc import Mapping, Set

from pydantic import BaseModel

from klorb.agents.definition import AgentRestrictions

SCRATCHPAD_CATEGORY = "SCRATCHPAD"
"""`Tool.category()` value `enforce_readonly_tools` always lets through regardless of
`is_read_only()` -- see `AgentRestrictions.enforce_readonly_tools`'s own docstring for why."""


class ToolMetadata(BaseModel):
    """The two `Tool` properties `compute_child_tool_set` filters on, for one tool in a
    parent's effective tool set."""

    category: str
    is_read_only: bool


def _resolve_named_subset(
    parent_effective: Set[str], requested: list[str] | None,
) -> set[str]:
    """`requested is None` inherits all of `parent_effective`; otherwise the result is
    `requested` intersected with `parent_effective` -- an explicitly empty `requested` list
    therefore yields an empty set, never "unspecified." Shared by every "keep these names, or
    everything, but never more than the parent has" filter below."""
    if requested is None:
        return set(parent_effective)
    return set(requested) & set(parent_effective)


def compute_child_skill_set(
    parent_skills: Set[str], restrict_to: AgentRestrictions,
) -> set[str]:
    """Return the subagent's effective skill set: `restrict_to.skills` intersected against
    `parent_skills`, or all of `parent_skills` when `restrict_to.skills` is `None`."""
    return _resolve_named_subset(parent_skills, restrict_to.skills)


def compute_child_subagent_roles(
    parent_subagent_roles: Set[str], restrict_to: AgentRestrictions,
) -> set[str]:
    """Return the roles the subagent may itself pass to `CreateSubagent`:
    `restrict_to.subagent_roles` intersected against `parent_subagent_roles` (the roles the
    *parent* was allowed to launch, not a fresh `agents.json` lookup of the child role's own
    nominal `subagent_roles`), or all of `parent_subagent_roles` when
    `restrict_to.subagent_roles` is `None`."""
    return _resolve_named_subset(parent_subagent_roles, restrict_to.subagent_roles)


def compute_child_tool_set(
    parent_tools: Mapping[str, ToolMetadata], restrict_to: AgentRestrictions,
) -> set[str]:
    """Return the subagent's effective tool set, narrowing `parent_tools` by every filter
    `restrict_to` specifies, each applied in addition to (not instead of) the others:

    * `restrict_to.tools`: keep only these names (or all of `parent_tools` if `None`).
    * `restrict_to.tool_categories`: further keep only tools whose `category` is one of
      these (skipped if `None`).
    * `restrict_to.enforce_readonly_tools`: further keep only tools with `is_read_only`
      `True`.
      (skipped if `False`).
    """
    effective = _resolve_named_subset(set(parent_tools), restrict_to.tools)
    if restrict_to.tool_categories is not None:
        allowed_categories = set(restrict_to.tool_categories)
        effective &= {
            name for name in effective if parent_tools[name].category in allowed_categories
        }
    if restrict_to.enforce_readonly_tools:
        effective &= {
            name for name in effective
            if parent_tools[name].is_read_only
        }
    return effective
