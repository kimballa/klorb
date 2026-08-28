# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.web.search — WebSearchTool."""

import pytest

from klorb.permissions.domain_access import DomainRules
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.web.search import WebSearchTool


def _context(
    *, deny: list[str] | None = None,
    max_results: int | None = None, max_uses: int | None = None,
) -> ToolSetupContext:
    session_config = SessionConfig()
    if deny is not None:
        session_config.web_domain_rules = DomainRules(deny=deny)
    process_config = ProcessConfig()
    if max_results is not None:
        process_config.web_search_max_results = max_results
    if max_uses is not None:
        process_config.web_search_max_uses = max_uses
    return ToolSetupContext(process_config=process_config, session_config=session_config)


def test_execution_mode_is_server() -> None:
    assert WebSearchTool(_context()).execution_mode() == "server"


def test_apply_raises() -> None:
    with pytest.raises(RuntimeError):
        WebSearchTool(_context()).apply({})


def test_provider_definition_default_shape() -> None:
    definition = WebSearchTool(_context()).provider_definition()

    assert definition == {
        "type": "openrouter:web_search",
        "parameters": {"max_results": 10, "max_uses": 3},
    }


def test_provider_definition_uses_configured_max_results_and_uses() -> None:
    definition = WebSearchTool(_context(max_results=5, max_uses=1)).provider_definition()

    assert definition["parameters"]["max_results"] == 5
    assert definition["parameters"]["max_uses"] == 1


def test_provider_definition_includes_excluded_domains_from_web_domain_denylist() -> None:
    definition = WebSearchTool(_context(deny=["evil.example.com"])).provider_definition()

    assert definition["parameters"]["excluded_domains"] == ["evil.example.com"]


def test_provider_definition_omits_excluded_domains_when_denylist_empty() -> None:
    definition = WebSearchTool(_context()).provider_definition()

    assert "excluded_domains" not in definition["parameters"]


def test_summary_before_results() -> None:
    assert WebSearchTool(_context()).summary({}) == "WebSearch"


def test_summary_with_results() -> None:
    result = [{"title": "A", "url": "https://a.example.com", "content": "excerpt"}]
    assert WebSearchTool(_context()).summary({}, result) == "Web search: 1 result(s)"


def test_detail_view_renders_numbered_list() -> None:
    result = [
        {"title": "First", "url": "https://a.example.com", "content": "First line.\nMore text."},
        {"title": "Second", "url": "https://b.example.com", "content": None},
    ]

    detail = WebSearchTool(_context()).detail_view({}, result)

    assert detail == (
        "1. First\n"
        "   https://a.example.com\n"
        "   First line.\n"
        "2. Second\n"
        "   https://b.example.com"
    )


def test_detail_view_no_results() -> None:
    assert WebSearchTool(_context()).detail_view({}, []) == "Web search: no results."
