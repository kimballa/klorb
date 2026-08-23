# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.util.response_headers.format_header_lines()."""

import logging

import pytest

from klorb.tools.util.response_headers import format_header_lines


def test_renders_keys_in_key_order() -> None:
    result = {"b": 2, "a": 1}

    lines = format_header_lines(result, ("a", "b"))

    assert lines == ["a: 1", "b: 2"]


def test_skips_key_order_entries_absent_from_result() -> None:
    result = {"a": 1}

    lines = format_header_lines(result, ("a", "b"))

    assert lines == ["a: 1"]


def test_skips_none_valued_keys_even_when_in_key_order() -> None:
    result = {"a": 1, "b": None}

    lines = format_header_lines(result, ("a", "b"))

    assert lines == ["a: 1"]


def test_unmentioned_key_still_emitted_at_the_end_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = {"a": 1, "surprise": "value"}

    with caplog.at_level(logging.WARNING):
        lines = format_header_lines(result, ("a",))

    assert lines == ["a: 1", "surprise: value"]
    assert "surprise" in caplog.text


def test_known_elsewhere_key_is_excluded_without_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = {"a": 1, "content": "big blob"}

    with caplog.at_level(logging.WARNING):
        lines = format_header_lines(result, ("a",), known_elsewhere=frozenset({"content"}))

    assert lines == ["a: 1"]
    assert caplog.text == ""


def test_unmentioned_none_valued_key_is_not_emitted_or_warned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = {"a": 1, "surprise": None}

    with caplog.at_level(logging.WARNING):
        lines = format_header_lines(result, ("a",))

    assert lines == ["a: 1"]
    assert caplog.text == ""


def test_bool_values_render_lowercase() -> None:
    result = {"a": True, "b": False}

    lines = format_header_lines(result, ("a", "b"))

    assert lines == ["a: true", "b: false"]
