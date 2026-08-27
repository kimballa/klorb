# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.create_file_core."""

from pathlib import Path

import pytest

from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.util import CreateFileCore


def test_creates_a_new_file(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    result = CreateFileCore().apply(
        file_path, {"content": "a\nb\nc\n"}, subject=str(file_path), edit_hint="EditFile")

    assert file_path.read_text() == "a\nb\nc\n"
    assert result["created"] is True
    assert result["total_lines"] == 3


def test_content_is_line_numbered(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    result = CreateFileCore().apply(
        file_path, {"content": "a\nb\nc\n"}, subject=str(file_path), edit_hint="EditFile")

    assert result["content"] == "1|a\n2|b\n3|c"


def test_creates_an_empty_file(tmp_path: Path) -> None:
    file_path = tmp_path / "empty.txt"

    result = CreateFileCore().apply(
        file_path, {"content": ""}, subject=str(file_path), edit_hint="EditFile")

    assert file_path.read_text() == ""
    assert result["total_lines"] == 0


def test_raises_if_file_already_exists(tmp_path: Path) -> None:
    file_path = tmp_path / "existing.txt"
    file_path.write_text("old\n")

    with pytest.raises(FileExistsError, match="already exists"):
        CreateFileCore().apply(
            file_path, {"content": "new\n"}, subject=str(file_path), edit_hint="EditFile")

    assert file_path.read_text() == "old\n"


def test_already_exists_error_names_the_given_edit_hint(tmp_path: Path) -> None:
    file_path = tmp_path / "existing.txt"
    file_path.write_text("old\n")

    with pytest.raises(FileExistsError, match="use EditMemory to modify it"):
        CreateFileCore().apply(
            file_path, {"content": "new\n"}, subject=str(file_path), edit_hint="EditMemory")


def test_creates_missing_parent_directories(tmp_path: Path) -> None:
    file_path = tmp_path / "a" / "b" / "c" / "new.txt"

    CreateFileCore().apply(
        file_path, {"content": "hi\n"}, subject=str(file_path), edit_hint="EditFile")

    assert file_path.read_text() == "hi\n"


def test_parameter_properties_exposes_content() -> None:
    assert "content" in CreateFileCore().parameter_properties()


# --- CreateFileCore.format_result() ---


def test_format_create_result_renders_headers_before_the_content_block(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    result = CreateFileCore().apply(
        file_path, {"content": "a\nb\n"}, subject=str(file_path), edit_hint="EditFile")
    result["filename"] = "new.txt"

    rendered = CreateFileCore().format_result(result)
    header, content_block = rendered.split("\n\n")

    assert header.splitlines() == [
        "filename: new.txt", "created: true", "total_lines: 2",
        "note: No verification ReadFile needed.",
    ]
    assert content_block == "Created content:\n========\n1|a\n2|b"


def test_format_create_result_omits_absent_optional_fields(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    result = CreateFileCore().apply(
        file_path, {"content": "a\n"}, subject=str(file_path), edit_hint="EditFile")

    rendered = CreateFileCore().format_result(result)

    assert "warning" not in rendered


def test_note_is_always_present_on_success(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    result = CreateFileCore().apply(
        file_path, {"content": "a\n"}, subject=str(file_path), edit_hint="EditFile")

    rendered = CreateFileCore().format_result(result)

    assert "note: No verification ReadFile needed." in rendered


def test_update_args_truncates_content_on_success() -> None:
    args = {"filename": "f.txt", "content": "a\nb\nc\n"}

    updated = CreateFileCore().update_args(args, ToolCallErrorInfo(is_error=False, is_retryable=False))

    assert updated == {
        "filename": "f.txt",
        'content': 'a… <line 1 of 3; applied -- see Applied diff in response>',
    }


def test_update_args_leaves_args_unchanged_on_error() -> None:
    args = {"filename": "f.txt", "content": "a\nb\nc\n"}

    updated = CreateFileCore().update_args(
        args, ToolCallErrorInfo(is_error=True, is_retryable=False, error_category="validation"))

    assert updated == args
