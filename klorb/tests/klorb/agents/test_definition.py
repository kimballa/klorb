# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.definition: `AgentDefinition`/`AgentRestrictions` validation."""

from klorb.agents.definition import (
    AgentCapabilities,
    AgentDefinition,
    AgentRestrictions,
    agent_event_configs,
    agent_hook_configs,
)
from klorb.hooks.config import FileSystemModifiedEventConfig


def test_agent_restrictions_defaults_to_unspecified_everywhere() -> None:
    restrictions = AgentRestrictions()

    assert restrictions.tools is None
    assert restrictions.tool_categories is None
    assert restrictions.skills is None
    assert restrictions.subagent_roles is None
    assert restrictions.enforce_readonly_tools is False


def test_agent_restrictions_distinguishes_empty_list_from_unspecified() -> None:
    restrictions = AgentRestrictions.model_validate({"tools": []})

    assert restrictions.tools == []
    assert restrictions.skills is None


def test_agent_definition_restrict_to_defaults_to_unrestricted() -> None:
    definition = AgentDefinition(name="explorer", default_model="some/model")

    assert definition.restrict_to == AgentRestrictions()
    assert definition.max_copies is None
    assert definition.agent_capabilities == AgentCapabilities()
    assert definition.hooks == {}
    assert definition.events == {}


def test_agent_definition_round_trips_max_copies_from_json_shaped_dict() -> None:
    definition = AgentDefinition.model_validate({
        "name": "operator", "default_model": "some/model", "max_copies": 1,
    })

    assert definition.max_copies == 1


def test_agent_capabilities_default_to_false() -> None:
    capabilities = AgentCapabilities()

    assert capabilities.accepts_tasks is False
    assert capabilities.assigns_tasks is False
    assert capabilities.see_group_tasks is False


def test_agent_definition_round_trips_agent_capabilities_from_json_shaped_dict() -> None:
    definition = AgentDefinition.model_validate({
        "name": "operator",
        "default_model": "some/model",
        "agent_capabilities": {
            "accepts_tasks": True,
            "assigns_tasks": True,
            "see_group_tasks": True,
        },
    })

    assert definition.agent_capabilities.accepts_tasks is True
    assert definition.agent_capabilities.assigns_tasks is True
    assert definition.agent_capabilities.see_group_tasks is True


def test_agent_definition_round_trips_from_json_shaped_dict() -> None:
    definition = AgentDefinition.model_validate({
        "name": "explorer",
        "default_model": "some/model",
        "restrict_to": {
            "tools": ["ReadFile"],
            "tool_categories": ["FILES"],
            "skills": ["internal:foo"],
            "subagent_roles": ["explorer"],
            "enforce_readonly_tools": True,
        },
        "agent_capabilities": {"allow_subagents": True},
    })

    assert definition.restrict_to.tools == ["ReadFile"]
    assert definition.restrict_to.tool_categories == ["FILES"]
    assert definition.restrict_to.skills == ["internal:foo"]
    assert definition.restrict_to.subagent_roles == ["explorer"]
    assert definition.restrict_to.enforce_readonly_tools is True
    assert definition.agent_capabilities.allow_subagents is True


def test_agent_hook_configs_parses_the_definitions_own_hooks_field() -> None:
    definition = AgentDefinition.model_validate({
        "name": "reviewer",
        "default_model": "some/model",
        "hooks": {"onToolUse": [{"type": "chat", "prompt": "watch it"}]},
    })

    result = agent_hook_configs(definition)

    assert result["onToolUse"][0].prompt == "watch it"
    assert result["onToolUse"][0].is_heritable is False  # default_is_heritable=False


def test_agent_event_configs_parses_the_definitions_own_events_field() -> None:
    definition = AgentDefinition.model_validate({
        "name": "pair_programmer",
        "default_model": "some/model",
        "events": {
            "FileSystemModified": [
                {"watch": ".", "applyGitignore": True, "action": {"type": "chat", "prompt": "x"}},
            ],
        },
    })

    result = agent_event_configs(definition)

    fs_event = result["FileSystemModified"][0]
    assert isinstance(fs_event, FileSystemModifiedEventConfig)
    assert fs_event.watch == "."
    assert fs_event.apply_gitignore is True
