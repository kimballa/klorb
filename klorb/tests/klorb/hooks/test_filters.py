# © Copyright 2026 Aaron Kimball
from klorb.hooks.config import HookConfigFilter
from klorb.hooks.filters import evaluate_filter


def test_no_fields_set_is_vacuously_true() -> None:
    assert evaluate_filter(HookConfigFilter(), "anything") is True


def test_matches_requires_exact_equality() -> None:
    assert evaluate_filter(HookConfigFilter(matches="definitely done"), "definitely done") is True
    assert evaluate_filter(HookConfigFilter(matches="definitely done"), "definitely done!") is False


def test_pattern_uses_regex_search() -> None:
    assert evaluate_filter(HookConfigFilter(pattern="^foo"), "foobar") is True
    assert evaluate_filter(HookConfigFilter(pattern="^foo"), "barfoo") is False


def test_contains_is_a_substring_check() -> None:
    assert evaluate_filter(HookConfigFilter(contains="bar"), "foobarbaz") is True
    assert evaluate_filter(HookConfigFilter(contains="qux"), "foobarbaz") is False


def test_any_is_true_if_one_nested_filter_passes() -> None:
    filter_ = HookConfigFilter(any=[HookConfigFilter(matches="a"), HookConfigFilter(matches="b")])
    assert evaluate_filter(filter_, "b") is True
    assert evaluate_filter(filter_, "c") is False


def test_all_requires_every_nested_filter_to_pass() -> None:
    filter_ = HookConfigFilter(all=[HookConfigFilter(contains="foo"), HookConfigFilter(contains="bar")])
    assert evaluate_filter(filter_, "foobar") is True
    assert evaluate_filter(filter_, "foo") is False


def test_not_negates_the_nested_filter() -> None:
    filter_ = HookConfigFilter.model_validate({"not": {"matches": "definitely done"}})
    assert evaluate_filter(filter_, "still working") is True
    assert evaluate_filter(filter_, "definitely done") is False


def test_multiple_fields_on_one_filter_combine_with_and() -> None:
    filter_ = HookConfigFilter(contains="foo", pattern="^f")
    assert evaluate_filter(filter_, "foobar") is True
    assert evaluate_filter(filter_, "barfoo") is False


def test_nested_any_all_not_compose() -> None:
    filter_ = HookConfigFilter.model_validate({
        "all": [
            {"contains": "foo"},
            {"not": {"matches": "foo-done"}},
        ],
    })
    assert evaluate_filter(filter_, "foobar") is True
    assert evaluate_filter(filter_, "foo-done") is False
