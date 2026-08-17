# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.search_tools."""
from collections.abc import Callable

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.registry import ToolRegistry
from klorb.tools.search_tools import SearchToolsTool
from klorb.tools.setup_context import ToolSetupContext


def _tool(make_session_config: Callable[..., SessionConfig]) -> SearchToolsTool:
    session_config = make_session_config()
    registry = ToolRegistry.discover_tools(ProcessConfig(), session_config)
    session = Session(config=session_config, tool_registry=registry)
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=session_config, session=session)
    return SearchToolsTool(context)


def test_exact_canonical_name_returns_that_tool_directly(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    result = _tool(make_session_config).apply({"queries": ["ReplaceAll"]})

    assert result["match_count"] == 1
    assert result["results"][0]["name"] == "ReplaceAll"
    assert "parameters" in result["results"][0]
    assert "description" in result["results"][0]


def test_exact_alias_resolves_to_canonical_tool(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    """`WriteFile` is an alias for `CreateFile`; an exact single-query alias match should
    return `CreateFile`'s own definition, not perform a keyword search."""
    result = _tool(make_session_config).apply({"queries": ["WriteFile"]})

    assert result["match_count"] == 1
    assert result["results"][0]["name"] == "CreateFile"


def test_keyword_search_matches_description_text(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    result = _tool(make_session_config).apply({"queries": ["backreferences"]})

    names = {r["name"] for r in result["results"]}
    assert "ReplaceAll" in names


def test_multiple_queries_never_take_the_exact_match_shortcut(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    """The exact-name fast path only applies to a single-entry queries list -- two queries
    always go through the keyword search, even if one of them is an exact tool name."""
    result = _tool(make_session_config).apply({"queries": ["ReplaceAll", "nonexistent-xyz"]})

    names = {r["name"] for r in result["results"]}
    assert "ReplaceAll" in names
    assert result["match_count"] == len(names)


def test_no_match_returns_empty_results(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    result = _tool(make_session_config).apply({"queries": ["definitely-not-a-real-tool-xyz"]})

    assert result["match_count"] == 0
    assert result["results"] == []


def test_empty_queries_raises(make_session_config: Callable[..., SessionConfig]) -> None:
    with pytest.raises(ValueError, match="non-empty array"):
        _tool(make_session_config).apply({"queries": []})


def test_requires_a_tool_registry() -> None:
    session_config = SessionConfig()
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=session_config)

    with pytest.raises(Exception, match="ToolRegistry"):
        SearchToolsTool(context).apply({"queries": ["ReplaceAll"]})


def test_name_and_parameters(make_session_config: Callable[..., SessionConfig]) -> None:
    tool = _tool(make_session_config)

    assert tool.name() == "SearchTools"
    assert tool.parameters()["required"] == ["queries"]


def test_summary_on_success(make_session_config: Callable[..., SessionConfig]) -> None:
    tool = _tool(make_session_config)
    result = tool.apply({"queries": ["ReplaceAll"]})

    assert "1 match" in tool.summary({"queries": ["ReplaceAll"]}, result)


def test_summary_on_failure_includes_the_error(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    tool = _tool(make_session_config)

    assert tool.summary({"queries": ["x"]}, error="boom") == "Search tools: ['x'] failed: boom"
