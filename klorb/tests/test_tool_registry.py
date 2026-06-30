# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.registry."""

import fixtures.sample_tools as sample_tools_package

from klorb.tools.registry import ToolRegistry


def test_discovers_tools_in_package() -> None:
    registry = ToolRegistry(package=sample_tools_package)

    names = {tool.name() for tool in registry.tools()}

    assert names == {"echo", "add"}


def test_get_returns_tool_by_name() -> None:
    registry = ToolRegistry(package=sample_tools_package)

    echo_tool = registry.get("echo")

    assert echo_tool.apply({"message": "hi"}) == "hi"


def test_tool_definitions_handles_json_schema_parameters() -> None:
    registry = ToolRegistry(package=sample_tools_package)

    definitions = {d["function"]["name"]: d for d in registry.tool_definitions()}

    echo_def = definitions["echo"]
    assert echo_def["type"] == "function"
    assert echo_def["function"]["description"] == "Echoes back the given message."
    assert echo_def["function"]["parameters"]["required"] == ["message"]


def test_tool_definitions_handles_pydantic_parameters() -> None:
    registry = ToolRegistry(package=sample_tools_package)

    definitions = {d["function"]["name"]: d for d in registry.tool_definitions()}

    add_def = definitions["add"]
    assert set(add_def["function"]["parameters"]["properties"]) == {"a", "b"}


def test_default_registry_discovers_production_tools() -> None:
    registry = ToolRegistry()

    names = {tool.name() for tool in registry.tools()}

    assert "ReadFile" in names
