# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.read_file_core.format_read_result()."""

from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.util import ReadFileCore, format_read_result


def test_renders_base_fields_and_content() -> None:
    result = {
        "start_line": 1, "end_line": 2, "total_lines": 2, "truncated": False,
        "content": "1|a\n2|b",
    }

    rendered = format_read_result(result)

    assert rendered == (
        "start_line: 1\nend_line: 2\ntotal_lines: 2\ntruncated: false\n\n1|a\n2|b")


def test_renders_truncation_fields_when_present() -> None:
    result = {
        "start_line": 1, "end_line": 40, "total_lines": 400, "truncated": True,
        "truncation_cause": "The response ended mid-line.", "next_start_line": 41,
        "content": "1|a",
    }

    rendered = format_read_result(result)

    header, _, content = rendered.partition("\n\n")
    assert header == (
        "start_line: 1\nend_line: 40\ntotal_lines: 400\ntruncated: true\n"
        "truncation_cause: The response ended mid-line.\nnext_start_line: 41")
    assert content == "1|a"


def test_bracketed_identity_keys_appear_only_when_present_and_in_order() -> None:
    result = {
        "namespace": "workspace", "filename": "notes.md",
        "start_line": 1, "end_line": 1, "total_lines": 1, "truncated": False, "content": "1|x",
    }

    rendered = format_read_result(result)

    header, _, _ = rendered.partition("\n\n")
    assert header.splitlines()[:2] == ["namespace: workspace", "filename: notes.md"]


def test_omits_absent_bracketed_keys() -> None:
    result = {
        "start_line": 1, "end_line": 1, "total_lines": 1, "truncated": False, "content": "1|x",
    }

    rendered = format_read_result(result)

    assert "namespace" not in rendered
    assert "filename" not in rendered


def test_empty_content_still_yields_trailing_blank_line() -> None:
    result = {
        "start_line": 1, "end_line": 0, "total_lines": 0, "truncated": False, "content": "",
    }

    rendered = format_read_result(result)

    assert rendered == "start_line: 1\nend_line: 0\ntotal_lines: 0\ntruncated: false\n\n"


def test_update_args_leaves_args_unchanged_on_success() -> None:
    args = {"filename": "f.txt", "start_line": 1, "end_line": 10}

    updated = ReadFileCore(200, 2000).update_args(
        args, ToolCallErrorInfo(is_error=False, is_retryable=False))

    assert updated == args


def test_update_args_leaves_args_unchanged_on_error() -> None:
    args = {"filename": "f.txt", "start_line": 1, "end_line": 10}

    updated = ReadFileCore(200, 2000).update_args(
        args, ToolCallErrorInfo(is_error=True, is_retryable=False, error_category="validation"))

    assert updated == args
