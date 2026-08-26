# © Copyright 2026 Aaron Kimball
"""Tests for klorb.agents.registry: parsing `agents.json` (schema `klorb-agents`) into an
`AgentRegistry`."""

from fixtures.sample_agents import SAMPLE_AGENTS_JSON

from klorb.agents.definition import AgentCapabilities, agent_event_configs
from klorb.agents.registry import AgentRegistry, get_agent_capabilities, get_agent_registry
from klorb.hooks.config import FileSystemModifiedEventConfig


def test_get_returns_agent_definition_by_name() -> None:
    registry = AgentRegistry(SAMPLE_AGENTS_JSON)

    explorer = registry.get("test_explorer")

    assert explorer is not None
    assert explorer.default_model == "test/explorer-model"
    assert explorer.agent_capabilities.allow_subagents is True
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


def test_get_agent_capabilities_reads_the_packaged_operator_and_explorer_roles() -> None:
    operator = get_agent_capabilities("operator")
    explorer = get_agent_capabilities("explorer")

    assert operator == AgentCapabilities(
        allow_subagents=True, accepts_tasks=True, assigns_tasks=True,
        see_group_tasks=True, send_messages=True)
    assert explorer == AgentCapabilities(allow_subagents=True)


def test_get_agent_capabilities_defaults_to_all_false_for_an_undefined_role() -> None:
    assert get_agent_capabilities("no_such_role") == AgentCapabilities()


def test_pair_programmer_role_may_launch_only_explorer_subagents() -> None:
    registry = get_agent_registry()

    pair_programmer = registry.get("pair_programmer")

    assert pair_programmer is not None
    assert pair_programmer.default_model == "klorb-default/normal"
    assert pair_programmer.agent_capabilities.allow_subagents is True
    assert pair_programmer.restrict_to.subagent_roles == ["explorer"]
    assert get_agent_capabilities("pair_programmer") == AgentCapabilities(
        allow_subagents=True, accepts_tasks=False, assigns_tasks=True,
        see_group_tasks=True, send_messages=True)


def test_pair_programmer_role_grants_a_workspace_wide_gitignore_filtered_file_watch() -> None:
    """A regression check on the hand-authored `pair_programmer` agents.json entry: its `events`
    field must parse to a `FileSystemModified` entry watching the whole workspace with gitignore
    filtering on, not just look right to a human reading the JSON."""
    registry = get_agent_registry()

    pair_programmer = registry.get("pair_programmer")

    assert pair_programmer is not None
    events = agent_event_configs(pair_programmer)
    fs_event = events["FileSystemModified"][0]
    assert isinstance(fs_event, FileSystemModifiedEventConfig)
    assert fs_event.watch == "."
    assert fs_event.apply_gitignore is True


def test_operator_role_may_launch_pair_programmer() -> None:
    registry = get_agent_registry()

    operator = registry.get("operator")

    assert operator is not None
    assert operator.restrict_to.subagent_roles is not None
    assert "pair_programmer" in operator.restrict_to.subagent_roles
