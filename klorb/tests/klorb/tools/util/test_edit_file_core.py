# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.edit_file_core.EditFileCore.format_result() and update_args()."""

from pathlib import Path

from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.tool import _ARG_PREVIEW_CHARS
from klorb.tools.util import EditFileCore


def _apply(tmp_path: Path, initial: str, **args: object) -> dict:
    file_path = tmp_path / "sample.txt"
    file_path.write_text(initial)
    return EditFileCore().apply(
        file_path, args, subject=str(file_path), reread_hint="re-read", create_hint="CreateFile")


def test_renders_identity_and_geometry_headers_before_the_diff(tmp_path: Path) -> None:
    result = _apply(tmp_path, "a\nb\nc\n", old_text="b", new_text="B")
    result["filename"] = "sample.txt"

    rendered = EditFileCore().format_result(result)
    header, diff_block = rendered.split("\n\n")

    assert header.splitlines()[0] == "filename: sample.txt"
    assert header.splitlines()[1] == "edit_success: true"
    assert "start_line: 2" in header
    # post_edit_content duplicates the diff's added lines, so it isn't rendered on the wire.
    assert "Post-edit content" not in rendered
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


def test_update_args_anchors_long_values_to_their_first_line_on_success() -> None:
    args = {
        "filename": "f.txt",
        "old_text": "def render():\n    return base + extra\n",
        "old_text_start": "def render():\n    return base + extra\n",
        "old_text_end": "def render():\n    return base + extra\n",
        "new_text": "x" * 120,
    }

    updated = EditFileCore().update_args(args, ToolCallErrorInfo(is_error=False, is_retryable=False))

    for name in ("old_text", "old_text_start", "old_text_end"):
        assert updated[name] == (
            "def render():… <line 1 of 2; applied -- see Applied diff in response>")
    assert updated["new_text"] == (
        "x" * _ARG_PREVIEW_CHARS + "… <line 1 of 1; applied -- see Applied diff in response>")
    assert updated["filename"] == "f.txt"


def test_update_args_passes_short_single_line_values_through() -> None:
    args = {"filename": "f.txt", "old_text": "b", "new_text": "B"}

    updated = EditFileCore().update_args(args, ToolCallErrorInfo(is_error=False, is_retryable=False))

    assert updated == args


def test_update_args_leaves_args_unchanged_on_error() -> None:
    args = {"filename": "f.txt", "old_text": "b", "new_text": "B"}

    updated = EditFileCore().update_args(
        args, ToolCallErrorInfo(is_error=True, is_retryable=False, error_category="validation"))

    assert updated == args
