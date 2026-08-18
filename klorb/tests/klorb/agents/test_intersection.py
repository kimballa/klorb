# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.intersection: the standalone tool/skill/subagent-role narrowing
logic `CreateSubagent` uses to compute a subagent's effective capability sets from its
parent's.
"""

from klorb.agents.definition import AgentRestrictions
from klorb.agents.intersection import (
    ToolMetadata,
    compute_child_skill_set,
    compute_child_subagent_roles,
    compute_child_tool_set,
)

# --- compute_child_tool_set ---


def test_unspecified_tools_inherits_everything_the_parent_has() -> None:
    parent_tools = {
        "ReadFile": ToolMetadata(category="FILES", is_read_only=True),
        "EditFile": ToolMetadata(category="FILES", is_read_only=False),
    }

    result = compute_child_tool_set(parent_tools, AgentRestrictions())

    assert result == {"ReadFile", "EditFile"}


def test_empty_tools_list_means_no_tools_not_unspecified() -> None:
    parent_tools = {"ReadFile": ToolMetadata(category="FILES", is_read_only=True)}

    result = compute_child_tool_set(parent_tools, AgentRestrictions(tools=[]))

    assert result == set()


def test_tools_list_intersects_with_parent_never_adds() -> None:
    parent_tools = {
        "ReadFile": ToolMetadata(category="FILES", is_read_only=True),
        "EditFile": ToolMetadata(category="FILES", is_read_only=False),
    }

    result = compute_child_tool_set(
        parent_tools, AgentRestrictions(tools=["ReadFile", "Bash"]))

    assert result == {"ReadFile"}


def test_tool_categories_filters_to_matching_categories_only() -> None:
    parent_tools = {
        "ReadFile": ToolMetadata(category="FILES", is_read_only=True),
        "ListMemories": ToolMetadata(category="MEMORY", is_read_only=True),
    }

    result = compute_child_tool_set(
        parent_tools, AgentRestrictions(tool_categories=["FILES"]))

    assert result == {"ReadFile"}


def test_tools_and_tool_categories_combine_as_an_intersection() -> None:
    parent_tools = {
        "ReadFile": ToolMetadata(category="FILES", is_read_only=True),
        "EditFile": ToolMetadata(category="FILES", is_read_only=False),
        "ListMemories": ToolMetadata(category="MEMORY", is_read_only=True),
    }

    result = compute_child_tool_set(
        parent_tools,
        AgentRestrictions(tools=["ReadFile", "ListMemories"], tool_categories=["FILES"]))

    assert result == {"ReadFile"}


def test_enforce_readonly_tools_clamps_to_read_only_tools() -> None:
    parent_tools = {
        "ReadFile": ToolMetadata(category="FILES", is_read_only=True),
        "EditFile": ToolMetadata(category="FILES", is_read_only=False),
    }

    result = compute_child_tool_set(
        parent_tools, AgentRestrictions(enforce_readonly_tools=True))

    assert result == {"ReadFile"}


def test_enforce_readonly_tools_applies_after_tools_and_tool_categories() -> None:
    parent_tools = {
        "ReadFile": ToolMetadata(category="FILES", is_read_only=True),
        "EditFile": ToolMetadata(category="FILES", is_read_only=False),
    }

    result = compute_child_tool_set(
        parent_tools,
        AgentRestrictions(tool_categories=["FILES"], enforce_readonly_tools=True))

    assert result == {"ReadFile"}


# --- compute_child_skill_set ---


def test_unspecified_skills_inherits_everything_the_parent_has() -> None:
    result = compute_child_skill_set(
        {"internal:foo", "internal:bar"}, AgentRestrictions())

    assert result == {"internal:foo", "internal:bar"}


def test_empty_skills_list_means_no_skills_not_unspecified() -> None:
    result = compute_child_skill_set({"internal:foo"}, AgentRestrictions(skills=[]))

    assert result == set()


def test_skills_list_intersects_with_parent_never_adds() -> None:
    result = compute_child_skill_set(
        {"internal:foo"}, AgentRestrictions(skills=["internal:foo", "internal:bar"]))

    assert result == {"internal:foo"}


# --- compute_child_subagent_roles ---


def test_unspecified_subagent_roles_inherits_everything_the_parent_has() -> None:
    result = compute_child_subagent_roles({"explorer", "vision_assistant"}, AgentRestrictions())

    assert result == {"explorer", "vision_assistant"}


def test_empty_subagent_roles_list_means_no_roles_not_unspecified() -> None:
    result = compute_child_subagent_roles({"explorer"}, AgentRestrictions(subagent_roles=[]))

    assert result == set()


def test_subagent_roles_intersects_against_the_parents_own_grant_not_a_fresh_lookup() -> None:
    # Even though "vision_assistant" is being requested, it's excluded because it wasn't in
    # the *parent's own* effective roles -- the whole point of intersecting against the live
    # parent set rather than re-deriving from agents.json by role name.
    result = compute_child_subagent_roles(
        {"explorer"}, AgentRestrictions(subagent_roles=["explorer", "vision_assistant"]))

    assert result == {"explorer"}
