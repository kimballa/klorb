# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.placeholders."""

import pytest

from klorb.models.placeholders import resolve_placeholder_model

_KWARGS = {"fast": "vendor/fast-model", "normal": "vendor/normal-model", "heavy": "vendor/heavy-model",
           "current": "vendor/current-model"}


def test_literal_model_id_passes_through_unchanged() -> None:
    assert resolve_placeholder_model("vendor/some-model", **_KWARGS) == "vendor/some-model"


def test_resolves_fast_placeholder() -> None:
    assert resolve_placeholder_model("klorb-default/fast", **_KWARGS) == "vendor/fast-model"


def test_resolves_normal_placeholder() -> None:
    assert resolve_placeholder_model("klorb-default/normal", **_KWARGS) == "vendor/normal-model"


def test_resolves_heavy_placeholder() -> None:
    assert resolve_placeholder_model("klorb-default/heavy", **_KWARGS) == "vendor/heavy-model"


def test_resolves_current_placeholder() -> None:
    assert resolve_placeholder_model("klorb-default/current", **_KWARGS) == "vendor/current-model"


def test_unrecognized_placeholder_suffix_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Unrecognized placeholder model"):
        resolve_placeholder_model("klorb-default/bogus", **_KWARGS)
