# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session_statistics."""

from klorb.session_statistics import SessionStatistics


def test_record_usage_accumulates_server_tool_calls() -> None:
    stats = SessionStatistics()

    stats.record_usage(server_tool_calls={"web_search_requests": 2})
    stats.record_usage(server_tool_calls={"web_search_requests": 3, "other_tool": 1})

    assert stats.server_tool_calls == {"web_search_requests": 5, "other_tool": 1}


def test_record_usage_with_no_server_tool_calls_leaves_it_empty() -> None:
    stats = SessionStatistics()

    stats.record_usage(input_tokens=10, output_tokens=5, cost=0.01)

    assert stats.server_tool_calls == {}


def test_format_report_omits_server_tool_section_when_empty() -> None:
    report = SessionStatistics().format_report()

    assert "Server tool calls" not in report


def test_format_report_includes_server_tool_counts_and_cost_estimate() -> None:
    stats = SessionStatistics()
    stats.record_usage(server_tool_calls={"web_search_requests": 3})

    report = stats.format_report()

    assert "Server tool calls" in report
    assert "web_search_requests: 3" in report
    assert "$0.021" in report
