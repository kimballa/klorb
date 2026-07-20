# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.diff_lines."""

from klorb.tools.util import DIFF_CONTEXT_LINES, build_diff_hunks


def test_no_change_yields_no_hunks() -> None:
    hunks = build_diff_hunks(["a", "b", "c"], ["a", "b", "c"])

    assert hunks == []


def test_pure_insert_into_empty_subject() -> None:
    hunks = build_diff_hunks([], ["a", "b"])

    assert len(hunks) == 1
    lines = hunks[0].lines
    assert [line.kind for line in lines] == ["add", "add"]
    assert [line.new_lineno for line in lines] == [1, 2]
    assert all(line.old_lineno is None for line in lines)
    assert [line.text for line in lines] == ["a", "b"]


def test_pure_delete_of_whole_subject() -> None:
    hunks = build_diff_hunks(["a", "b"], [])

    assert len(hunks) == 1
    lines = hunks[0].lines
    assert [line.kind for line in lines] == ["del", "del"]
    assert [line.old_lineno for line in lines] == [1, 2]
    assert all(line.new_lineno is None for line in lines)


def test_single_line_replace_in_the_middle() -> None:
    old_lines = ["a", "b", "c"]
    new_lines = ["a", "B", "c"]

    hunks = build_diff_hunks(old_lines, new_lines, context=1)

    assert len(hunks) == 1
    kinds = [line.kind for line in hunks[0].lines]
    assert kinds == ["context", "del", "add", "context"]


def test_context_is_clamped_at_file_start_and_end() -> None:
    old_lines = ["a", "b"]
    new_lines = ["a", "B"]

    hunks = build_diff_hunks(old_lines, new_lines, context=10)

    assert len(hunks) == 1
    lines = hunks[0].lines
    assert [line.kind for line in lines] == ["context", "del", "add"]
    assert lines[0].text == "a"


def test_two_changes_within_context_merge_into_one_hunk() -> None:
    old_lines = [str(i) for i in range(20)]
    new_lines = list(old_lines)
    new_lines[2] = "X"
    new_lines[8] = "Y"

    hunks = build_diff_hunks(old_lines, new_lines, context=5)

    assert len(hunks) == 1


def test_two_changes_far_apart_stay_as_separate_hunks() -> None:
    old_lines = [str(i) for i in range(2 * DIFF_CONTEXT_LINES + 20)]
    new_lines = list(old_lines)
    new_lines[0] = "X"
    new_lines[-1] = "Y"

    hunks = build_diff_hunks(old_lines, new_lines)

    assert len(hunks) == 2


def test_default_context_matches_diff_context_lines_constant() -> None:
    old_lines = [str(i) for i in range(3 * DIFF_CONTEXT_LINES)]
    new_lines = list(old_lines)
    new_lines[len(new_lines) // 2] = "X"

    hunks = build_diff_hunks(old_lines, new_lines)

    assert len(hunks) == 1
    context_lines = [line for line in hunks[0].lines if line.kind == "context"]
    assert len(context_lines) == 2 * DIFF_CONTEXT_LINES


def test_line_numbers_are_one_indexed_and_match_original_positions() -> None:
    old_lines = ["a", "b", "c", "d"]
    new_lines = ["a", "x", "c", "d"]

    hunks = build_diff_hunks(old_lines, new_lines, context=10)

    lines = hunks[0].lines
    by_kind = {line.kind: line for line in lines if line.kind in ("del", "add")}
    assert by_kind["del"].old_lineno == 2
    assert by_kind["add"].new_lineno == 2
