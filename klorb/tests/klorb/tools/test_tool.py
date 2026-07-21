# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tool."""

import json

import pytest
from fixtures.sample_tools.echo_tool import EchoTool

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import (
    Tool,
    default_invalid_tool_call_detail,
    default_invalid_tool_call_summary,
    default_tool_call_detail,
    default_tool_call_summary,
    describe_tool_arg_json_error,
    truncate_lines,
)


def _decode_error(raw: str) -> json.JSONDecodeError:
    """Produce a real `json.JSONDecodeError` for `raw` by actually trying to parse it, so tests
    exercise `describe_tool_arg_json_error()` against the same exception shape
    `Session._run_tool_calls` catches, not a hand-built stand-in."""
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        return exc
    raise AssertionError(f"expected {raw!r} to fail to parse as JSON")


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

    assert json.loads(detail) == {"name": "echo", "args": {"message": "hi"}, "result": "hi"}


def test_default_detail_view_on_failure_is_pretty_printed_json_of_args_and_error() -> None:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())
    tool = EchoTool(context)

    detail = tool.detail_view({"message": "hi"}, result=None, error="boom")

    assert json.loads(detail) == {"name": "echo", "args": {"message": "hi"}, "error": "boom"}


def test_default_tool_call_summary_and_detail_are_usable_without_a_tool_instance() -> None:
    """The fallback formatters for a tool name a `ToolRegistry` doesn't recognize -- there's no
    `Tool` instance to call `.summary()`/`.detail_view()` on in that case."""
    assert default_tool_call_summary("Bogus", {"x": 1}, None) == "Bogus"
    assert default_tool_call_summary("Bogus", {"x": 1}, "no such tool") == "Bogus: no such tool"
    assert json.loads(default_tool_call_detail("Bogus", {"x": 1}, "ok", None)) == {
        "name": "Bogus", "args": {"x": 1}, "result": "ok"}
    assert json.loads(default_tool_call_detail("Bogus", {"x": 1}, None, "nope")) == {
        "name": "Bogus", "args": {"x": 1}, "error": "nope"}


def test_default_invalid_tool_call_summary_and_detail_show_the_raw_arguments() -> None:
    """The fallback formatters for a tool call whose `arguments` string failed to parse as
    JSON -- there's no parsed `args` dict to render, so the raw text is shown instead."""
    summary = default_invalid_tool_call_summary("Grep", "Expecting ',' delimiter: line 1 column 34")
    assert summary == "Invalid tool call generated: Grep: Expecting ',' delimiter: line 1 column 34"

    detail = default_invalid_tool_call_detail(
        "Grep", '{"pattern": "foo" "path": "."}', "Expecting ',' delimiter: line 1 column 34")
    assert "Invalid tool call generated: Grep" in detail
    assert '{"pattern": "foo" "path": "."}' in detail
    assert "Expecting ',' delimiter: line 1 column 34" in detail


def test_truncate_lines_leaves_short_text_unchanged() -> None:
    assert truncate_lines("a\nb\nc", max_lines=8) == "a\nb\nc"


def test_truncate_lines_leaves_text_at_the_exact_cap_unchanged() -> None:
    text = "\n".join(str(i) for i in range(8))
    assert truncate_lines(text, max_lines=8) == text


def test_truncate_lines_caps_long_text_with_an_ellipsis_line() -> None:
    text = "\n".join(str(i) for i in range(20))
    truncated = truncate_lines(text, max_lines=8)
    assert truncated == "\n".join(str(i) for i in range(8)) + "\n..."


# --- describe_tool_arg_json_error() (see docs/specs/tool-framework.md's
# "Tool-call argument parse errors" section) ---


def test_describe_tool_arg_json_error_offset_framing_names_the_break_point() -> None:
    raw = '{"pattern": "foo" "path": "."}'
    exc = _decode_error(raw)

    message = describe_tool_arg_json_error("Grep", raw, exc)

    assert f"line {exc.lineno}, column {exc.colno} (char {exc.pos})" in message
    assert exc.msg in message
    assert raw[max(0, exc.pos - 5):exc.pos + 5] in message
    assert "^" in message


def test_describe_tool_arg_json_error_caret_lands_under_the_offending_line_for_multiline_json() -> None:
    """A multi-line raw_arguments string (an edit tool's new_text carrying an unescaped literal
    newline, exactly as happens in practice) must render its caret directly beneath the line
    naming exc.lineno, not after the whole excerpt block -- see
    klorb.json_error_display.format_json_error_context, shared with
    klorb.schema_envelope's config-parse-error rendering."""
    raw = '{"filename": "foo.py",\n"new_text": "line-one\nline-two", "start_line": 1}'
    exc = _decode_error(raw)
    assert exc.lineno > 1  # the failure is on the raw string's second physical line

    message = describe_tool_arg_json_error("EditFile", raw, exc)

    lines = message.split("\n")
    error_line_index = next(
        i for i, line in enumerate(lines) if line.strip().startswith(f"{exc.lineno} |"))
    error_line = lines[error_line_index]
    caret_line = lines[error_line_index + 1]
    assert caret_line.strip().endswith("^")
    content_start = error_line.index("|") + 2
    assert caret_line.index("^") - content_start == exc.colno - 1


def test_describe_tool_arg_json_error_detects_xml_and_skips_common_mistakes() -> None:
    raw = "<tool_call><name>Grep</name></tool_call>"
    exc = _decode_error(raw)

    message = describe_tool_arg_json_error("Grep", raw, exc)

    assert "XML/markup" in message
    assert '"filename": "foo.py"' in message
    assert "Common JSON mistakes" not in message


def test_describe_tool_arg_json_error_common_mistakes_includes_escape_contrast() -> None:
    raw = '{"new_text": "print("hi")"}'
    exc = _decode_error(raw)

    message = describe_tool_arg_json_error("EditFile", raw, exc)

    assert "Common JSON mistakes" in message
    assert 'print(\\"hi\\")' in message


def test_describe_tool_arg_json_error_flags_edit_tool_args_when_present() -> None:
    raw = '{"filename": "foo.py", "new_text": "abc" "start_line": 1}'
    exc = _decode_error(raw)

    message = describe_tool_arg_json_error("EditFile", raw, exc)

    assert "edit tool call" in message


def test_describe_tool_arg_json_error_omits_edit_hint_when_no_edit_args_present() -> None:
    raw = '{"pattern": "foo" "path": "."}'
    exc = _decode_error(raw)

    message = describe_tool_arg_json_error("Grep", raw, exc)

    assert "edit-tool call" not in message
