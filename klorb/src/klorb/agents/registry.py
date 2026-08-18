# © Copyright 2026 Aaron Kimball
"""Loads `klorb/resources/agents.json` once into an immutable in-process catalog of
`AgentDefinition`s."""

import importlib.resources
from importlib.resources.abc import Traversable

from klorb.agents.definition import AGENT_SCHEMA_NAME, AgentCapabilities, AgentDefinition
from klorb.schema_envelope import parse_versioned_json

AGENTS_RESOURCE_PACKAGE = "klorb.resources"
AGENTS_RESOURCE_NAME = "agents.json"


class AgentRegistry:
    """Read-only catalog of every subagent role's `AgentDefinition`, keyed by name. Reads
    `agents.json` at most once, on first use, and caches it for the rest of this instance's
    lifetime. Reach the process-wide instance via `get_agent_registry()` rather than
    constructing one directly, so `agents.json` is parsed at most once per process.
    """

    def __init__(self, agents_resource: Traversable | None = None) -> None:
        self._agents_resource = agents_resource or (
            importlib.resources.files(AGENTS_RESOURCE_PACKAGE).joinpath(AGENTS_RESOURCE_NAME)
        )
        """Which file `_ensure_loaded` reads."""
        self._by_name: dict[str, AgentDefinition] | None = None

    def _ensure_loaded(self) -> None:
        if self._by_name is not None:
            return
        text = self._agents_resource.read_text(encoding="utf-8")
        data = parse_versioned_json(
            text, expected_schema_name=AGENT_SCHEMA_NAME, source=str(self._agents_resource))
        self._by_name = {
            entry.name: entry
            for entry in (AgentDefinition.model_validate(raw) for raw in data.get("agents", []))
        }

    def get(self, name: str) -> AgentDefinition | None:
        """Return the `AgentDefinition` for `name`, or `None` if no such role is defined."""
        self._ensure_loaded()
        assert self._by_name is not None
        return self._by_name.get(name)

    def names(self) -> list[str]:
        """Return every defined role name."""
        self._ensure_loaded()
        assert self._by_name is not None
        return list(self._by_name)


_registry = AgentRegistry()


def get_agent_registry() -> AgentRegistry:
    """Return the process-wide `AgentRegistry` singleton."""
    return _registry


def get_agent_capabilities(role_name: str) -> AgentCapabilities:
    """Return `role_name`'s `AgentCapabilities` from the process-wide registry, or an
    all-`False` default if `role_name` names no defined role."""
    definition = get_agent_registry().get(role_name)
    return definition.agent_capabilities if definition is not None else AgentCapabilities()
