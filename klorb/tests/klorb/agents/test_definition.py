# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.definition: `AgentDefinition`/`AgentRestrictions` validation,
particularly the "None means unspecified/inherit-all, [] means explicitly nothing" distinction
`klorb.agents.intersection` depends on."""

from klorb.agents.definition import AgentCapabilities, AgentDefinition, AgentRestrictions


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
    assert definition.allow_subagents is False
    assert definition.agent_capabilities == AgentCapabilities()


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
        "allow_subagents": True,
    })

    assert definition.restrict_to.tools == ["ReadFile"]
    assert definition.restrict_to.tool_categories == ["FILES"]
    assert definition.restrict_to.skills == ["internal:foo"]
    assert definition.restrict_to.subagent_roles == ["explorer"]
    assert definition.restrict_to.enforce_readonly_tools is True
    assert definition.allow_subagents is True
