# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.registry."""

import fixtures.sample_tools as sample_tools_package

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import NoSuchToolException
from klorb.tools.registry import ToolRegistry


def _registry(package=sample_tools_package) -> ToolRegistry:
    return ToolRegistry.discover_tools(ProcessConfig(), SessionConfig(), package=package)


def test_discovers_tools_in_package() -> None:
    registry = _registry()

    names = {tool.name() for tool in registry.tools()}

    assert names == {"echo", "add", "ask_permission", "ask_multi_permission"}


def test_instantiate_tool_returns_tool_by_name() -> None:
    registry = _registry()

    echo_tool = registry.instantiate_tool("echo")

    assert echo_tool.apply({"message": "hi"}) == "hi"


def test_instantiate_tool_builds_a_fresh_instance_each_call() -> None:
    registry = _registry()

    assert registry.instantiate_tool("echo") is not registry.instantiate_tool("echo")


def test_tool_definitions_handles_json_schema_parameters() -> None:
    registry = _registry()

    definitions = {d["function"]["name"]: d for d in registry.tool_definitions()}

    echo_def = definitions["echo"]
    assert echo_def["type"] == "function"
    assert echo_def["function"]["description"] == "Echoes back the given message."
    assert echo_def["function"]["parameters"]["required"] == ["message"]


def test_tool_definitions_handles_pydantic_parameters() -> None:
    registry = _registry()

    definitions = {d["function"]["name"]: d for d in registry.tool_definitions()}

    add_def = definitions["add"]
    assert set(add_def["function"]["parameters"]["properties"]) == {"a", "b"}


def test_default_registry_discovers_production_tools() -> None:
    registry = ToolRegistry.discover_tools(ProcessConfig(), SessionConfig())

    names = {tool.name() for tool in registry.tools()}

    assert "ReadFile" in names
    assert "Bash" in names
    assert "AskUserQuestions" in names
    assert {
        "ListMemories", "SearchMemories", "ReadMemory", "EditMemory", "CreateMemory",
        "ForgetMemory",
    } <= names


def test_registry_has_no_session_before_one_is_constructed() -> None:
    registry = _registry()
    assert registry.session is None
    assert registry.instantiate_tool("echo").context.session is None


def test_session_construction_backfills_the_registrys_session_reference() -> None:
    """A ToolRegistry is always built before the Session it's passed into, so the back-reference
    has to be set post-construction by Session.__init__ -- confirm it actually happens, and that
    every ToolSetupContext built afterward carries it."""
    registry = _registry()
    session = Session(config=SessionConfig(), tool_registry=registry)

    assert registry.session is session
    assert registry.instantiate_tool("echo").context.session is session


def test_init_builds_a_registry_from_a_subset_of_discovered_classes() -> None:
    """`__init__` takes a `dict[str, type[Tool]]` it clones, so a session-scoped registry can be
    built from a filtered subset of a bootstrap registry's classes without re-scanning a
    package -- the foundation for subagents getting a restricted tool set."""
    bootstrap = _registry()
    subset = {name: cls for name, cls in bootstrap._tool_classes.items() if name in {"echo", "add"}}

    registry = ToolRegistry(ProcessConfig(), SessionConfig(), subset)

    assert {tool.name() for tool in registry.tools()} == {"echo", "add"}
    # The registry cloned the dict, so mutating the caller's copy doesn't affect it.
    subset.clear()
    assert {tool.name() for tool in registry.tools()} == {"echo", "add"}


def test_init_holds_its_own_copy_of_the_tool_classes_dict() -> None:
    """The dict passed to `__init__` is cloned, not retained by reference -- a later change to
    the caller's dict must not surface in the registry."""
    bootstrap = _registry()
    classes = dict(bootstrap._tool_classes)
    registry = ToolRegistry(ProcessConfig(), SessionConfig(), classes)

    classes["bogus"] = bootstrap._tool_classes["echo"]  # type: ignore[index]

    try:
        registry.instantiate_tool("bogus")
    except NoSuchToolException:
        pass
    else:  # pragma: no cover - the registry must not have picked up the caller's mutation
        raise AssertionError("registry should not observe caller-side dict mutations")
