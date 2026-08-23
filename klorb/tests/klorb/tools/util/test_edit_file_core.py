# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.edit_file_core.format_edit_result()."""

from pathlib import Path

from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.util import EditFileCore, format_edit_result


def _apply(tmp_path: Path, initial: str, **args: object) -> dict:
    file_path = tmp_path / "sample.txt"
    file_path.write_text(initial)
    return EditFileCore().apply(
        file_path, args, subject=str(file_path), reread_hint="re-read", create_hint="CreateFile")


def test_renders_identity_and_geometry_headers_before_the_content_blocks(tmp_path: Path) -> None:
    result = _apply(tmp_path, "a\nb\nc\n", old_text="b", new_text="B")
    result["filename"] = "sample.txt"

    rendered = format_edit_result(result)
    header, post_edit_content, diff_block = rendered.split("\n\n")

    assert header.splitlines()[0] == "filename: sample.txt"
    assert header.splitlines()[1] == "edit_success: true"
    assert "start_line: 2" in header
    assert post_edit_content == "2|B"
    assert diff_block != ""


def test_created_and_optional_fields_appear_when_present(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    result = EditFileCore().apply(
        file_path, {"old_text": "", "new_text": "hi"}, subject=str(file_path),
        reread_hint="re-read", create_hint="CreateFile")

    rendered = format_edit_result(result)

    assert "created: true" in rendered


def test_optional_fields_absent_when_not_present(tmp_path: Path) -> None:
    result = _apply(tmp_path, "a\nb\nc\n", old_text="b", new_text="B")

    rendered = format_edit_result(result)

    assert "created" not in rendered
    assert "fuzzy_whitespace_match" not in rendered
    assert "whitespace:" not in rendered
    assert "warning" not in rendered


def test_note_is_always_present_on_success(tmp_path: Path) -> None:
    result = _apply(tmp_path, "a\nb\nc\n", old_text="b", new_text="B")

    rendered = format_edit_result(result)

    assert "note: No verification ReadFile needed." in rendered


def test_update_args_drops_everything_on_success() -> None:
    args = {"filename": "f.txt", "old_text": "b", "new_text": "B"}

    updated = EditFileCore().update_args(args, ToolCallErrorInfo(is_error=False, is_retryable=False))

    assert updated == {}


def test_update_args_leaves_args_unchanged_on_error() -> None:
    args = {"filename": "f.txt", "old_text": "b", "new_text": "B"}

    updated = EditFileCore().update_args(
        args, ToolCallErrorInfo(is_error=True, is_retryable=False, error_category="validation"))

    assert updated == args
