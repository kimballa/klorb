# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.search_core."""

import pytest

from klorb.tools.util.search_core import validate_queries


def test_validate_queries_accepts_a_list_of_strings() -> None:
    assert validate_queries(["hello", "world"]) == ["hello", "world"]


def test_validate_queries_wraps_a_bare_string_into_a_singleton_list() -> None:
    assert validate_queries("hello") == ["hello"]


def test_validate_queries_rejects_an_empty_list() -> None:
    with pytest.raises(ValueError, match="queries"):
        validate_queries([])


def test_validate_queries_stringifies_a_non_string_list_entry() -> None:
    assert validate_queries(["hello", 5]) == ["hello", "5"]
