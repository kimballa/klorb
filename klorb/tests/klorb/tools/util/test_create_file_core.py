# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.create_file_core."""

import json
from pathlib import Path

import pytest

from klorb.tools.util import CreateFileCore, DiffHunk, format_create_result


def test_creates_a_new_file(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    result = CreateFileCore().apply(
        file_path, {"content": "a\nb\nc\n"}, subject=str(file_path), edit_hint="EditFile")

    assert file_path.read_text() == "a\nb\nc\n"
    assert result["created"] is True
    assert result["total_lines"] == 3


def test_diff_is_an_all_add_hunk_matching_the_new_content(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"

    result = CreateFileCore().apply(
        file_path, {"content": "a\nb\nc\n"}, subject=str(file_path), edit_hint="EditFile")

    # Round-trips through JSON exactly like a persisted session's tool_response would.
    hunks = [DiffHunk.model_validate(hunk) for hunk in json.loads(json.dumps(result))["diff"]]
    assert len(hunks) == 1
    assert [line.kind for line in hunks[0].lines] == ["add", "add", "add"]
    assert [line.text for line in hunks[0].lines] == ["a", "b", "c"]


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


# --- format_create_result() ---


def test_format_create_result_renders_headers_before_the_diff_block(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    result = CreateFileCore().apply(
        file_path, {"content": "a\nb\n"}, subject=str(file_path), edit_hint="EditFile")
    result["filename"] = "new.txt"

    rendered = format_create_result(result)
    header, diff_block = rendered.split("\n\n")

    assert header.splitlines() == [
        "filename: new.txt", "created: true", "total_lines: 2",
        "note: No verification ReadFile needed.",
    ]
    assert diff_block != ""


def test_format_create_result_omits_absent_optional_fields(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    result = CreateFileCore().apply(
        file_path, {"content": "a\n"}, subject=str(file_path), edit_hint="EditFile")

    rendered = format_create_result(result)

    assert "warning" not in rendered


def test_note_is_always_present_on_success(tmp_path: Path) -> None:
    file_path = tmp_path / "new.txt"
    result = CreateFileCore().apply(
        file_path, {"content": "a\n"}, subject=str(file_path), edit_hint="EditFile")

    rendered = format_create_result(result)

    assert "note: No verification ReadFile needed." in rendered
