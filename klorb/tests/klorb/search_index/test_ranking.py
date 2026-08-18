# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.ranking."""

from klorb.search_index.ranking import reciprocal_rank_fusion


def test_single_list_preserves_order() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"]])

    assert [chunk_id for chunk_id, _score in fused] == ["a", "b", "c"]


def test_appearing_in_both_lists_outranks_appearing_in_one() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])

    ranked_ids = [chunk_id for chunk_id, _score in fused]
    assert ranked_ids[0] == "b"
    assert set(ranked_ids) == {"a", "b", "c"}


def test_first_place_in_a_list_scores_higher_than_second_place() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"]])
    scores = dict(fused)

    assert scores["a"] > scores["b"] > scores["c"]


def test_empty_lists_produce_no_results() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []


def test_ties_break_by_first_appearance_order() -> None:
    # "a" and "b" both appear only in the second list, at the same rank relative to each other
    # they had in the (non-overlapping) first list's absence
    fused = reciprocal_rank_fusion([["a"], ["b"]])

    scores = dict(fused)
    assert scores["a"] == scores["b"]
    assert [chunk_id for chunk_id, _score in fused] == ["a", "b"]
