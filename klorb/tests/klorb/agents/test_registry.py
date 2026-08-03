# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.registry: parsing `agents.json` (schema `klorb-agents`) into an
`AgentRegistry`. See docs/specs/subagents.md's "Configuration"
section."""

from fixtures.sample_agents import SAMPLE_AGENTS_JSON

from klorb.agents.registry import AgentRegistry, get_agent_registry


def test_get_returns_agent_definition_by_name() -> None:
    registry = AgentRegistry(SAMPLE_AGENTS_JSON)

    explorer = registry.get("test_explorer")

    assert explorer is not None
    assert explorer.default_model == "test/explorer-model"
    assert explorer.allow_subagents is True
    assert explorer.restrict_to.tools == ["ReadFile", "Grep"]
    assert explorer.restrict_to.enforce_readonly_tools is True


def test_get_returns_none_for_unknown_role() -> None:
    registry = AgentRegistry(SAMPLE_AGENTS_JSON)

    assert registry.get("no_such_role") is None


def test_names_lists_every_defined_role() -> None:
    registry = AgentRegistry(SAMPLE_AGENTS_JSON)

    assert set(registry.names()) == {"test_explorer", "test_vision_assistant"}


def test_agents_json_is_read_at_most_once() -> None:
    read_count = 0

    class _CountingResource:
        def read_text(self, encoding: str) -> str:
            nonlocal read_count
            read_count += 1
            return SAMPLE_AGENTS_JSON.read_text(encoding=encoding)

        def __str__(self) -> str:
            return "counting-resource"

    registry = AgentRegistry(_CountingResource())  # type: ignore[arg-type]

    registry.get("test_explorer")
    registry.names()
    registry.get("test_vision_assistant")

    assert read_count == 1


def test_get_agent_registry_returns_the_same_instance_every_call() -> None:
    assert get_agent_registry() is get_agent_registry()
