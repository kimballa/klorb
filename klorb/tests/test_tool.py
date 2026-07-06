# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tool."""

import json

import pytest
from fixtures.sample_tools.echo_tool import EchoTool

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, default_tool_call_detail, default_tool_call_summary, truncate_lines


def test_tool_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Tool()  # type: ignore[abstract, call-arg]


def test_subclass_constructor_takes_only_a_tool_setup_context() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())

    tool = EchoTool(context)

    assert tool.context is context


def test_default_summary_on_success_is_just_the_tool_name() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())
    tool = EchoTool(context)

    assert tool.summary({"message": "hi"}, result="hi", error=None) == "echo"


def test_default_summary_on_failure_includes_the_error() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())
    tool = EchoTool(context)

    assert tool.summary({"message": "hi"}, result=None, error="boom") == "echo: boom"


def test_default_detail_view_on_success_is_pretty_printed_json_of_args_and_result() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())
    tool = EchoTool(context)

    detail = tool.detail_view({"message": "hi"}, result="hi", error=None)

    assert json.loads(detail) == {"args": {"message": "hi"}, "result": "hi"}


def test_default_detail_view_on_failure_is_pretty_printed_json_of_args_and_error() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())
    tool = EchoTool(context)

    detail = tool.detail_view({"message": "hi"}, result=None, error="boom")

    assert json.loads(detail) == {"args": {"message": "hi"}, "error": "boom"}


def test_default_tool_call_summary_and_detail_are_usable_without_a_tool_instance() -> None:
    """The fallback formatters for a tool name a `ToolRegistry` doesn't recognize -- there's no
    `Tool` instance to call `.summary()`/`.detail_view()` on in that case."""
    assert default_tool_call_summary("Bogus", {"x": 1}, None) == "Bogus"
    assert default_tool_call_summary("Bogus", {"x": 1}, "no such tool") == "Bogus: no such tool"
    assert json.loads(default_tool_call_detail("Bogus", {"x": 1}, "ok", None)) == {
        "args": {"x": 1}, "result": "ok"}
    assert json.loads(default_tool_call_detail("Bogus", {"x": 1}, None, "nope")) == {
        "args": {"x": 1}, "error": "nope"}


def test_truncate_lines_leaves_short_text_unchanged() -> None:
    assert truncate_lines("a\nb\nc", max_lines=8) == "a\nb\nc"


def test_truncate_lines_leaves_text_at_the_exact_cap_unchanged() -> None:
    text = "\n".join(str(i) for i in range(8))
    assert truncate_lines(text, max_lines=8) == text


def test_truncate_lines_caps_long_text_with_an_ellipsis_line() -> None:
    text = "\n".join(str(i) for i in range(20))
    truncated = truncate_lines(text, max_lines=8)
    assert truncated == "\n".join(str(i) for i in range(8)) + "\n..."
