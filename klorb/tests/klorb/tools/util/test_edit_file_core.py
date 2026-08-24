# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.edit_file_core.EditFileCore.format_result()."""

from pathlib import Path

from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.util import EditFileCore


def _apply(tmp_path: Path, initial: str, **args: object) -> dict:
    file_path = tmp_path / "sample.txt"
    file_path.write_text(initial)
    return EditFileCore().apply(
        file_path, args, subject=str(file_path), reread_hint="re-read", create_hint="CreateFile")


def test_renders_identity_and_geometry_headers_before_the_content_blocks(tmp_path: Path) -> None:
    result = _apply(tmp_path, "a\nb\nc\n", old_text="b", new_text="B")
    result["filename"] = "sample.txt"

    rendered = EditFileCore().format_result(result)
    header, post_edit_content, diff_block = rendered.split("\n\n")

    assert header.splitlines()[0] == "filename: sample.txt"
    assert header.splitlines()[1] == "edit_success: true"
    assert "start_line: 2" in header
    assert post_edit_content == "Post-edit content:\n========\n2|B"
    assert diff_block != ""


def test_created_and_optional_fields_appear_when_present(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    result = EditFileCore().apply(
        file_path, {"old_text": "", "new_text": "hi"}, subject=str(file_path),
        reread_hint="re-read", create_hint="CreateFile")

    rendered = EditFileCore().format_result(result)

    assert "created: true" in rendered


def test_optional_fields_absent_when_not_present(tmp_path: Path) -> None:
    result = _apply(tmp_path, "a\nb\nc\n", old_text="b", new_text="B")

    rendered = EditFileCore().format_result(result)

    assert "created" not in rendered
    assert "fuzzy_whitespace_match" not in rendered
    assert "whitespace:" not in rendered
    assert "warning" not in rendered


def test_note_is_always_present_on_success(tmp_path: Path) -> None:
    result = _apply(tmp_path, "a\nb\nc\n", old_text="b", new_text="B")

    rendered = EditFileCore().format_result(result)

    assert "note: No verification ReadFile needed." in rendered


def test_update_args_truncates_old_and_new_text_on_success() -> None:
    args = {"filename": "f.txt", "old_text": "b", "new_text": "B"}

    updated = EditFileCore().update_args(args, ToolCallErrorInfo(is_error=False, is_retryable=False))

    placeholder = "(Applied correctly; arguments truncated. See response)"
    assert updated == {"filename": "f.txt", "old_text": placeholder, "new_text": placeholder}


def test_update_args_leaves_args_unchanged_on_error() -> None:
    args = {"filename": "f.txt", "old_text": "b", "new_text": "B"}

    updated = EditFileCore().update_args(
        args, ToolCallErrorInfo(is_error=True, is_retryable=False, error_category="validation"))

    assert updated == args
