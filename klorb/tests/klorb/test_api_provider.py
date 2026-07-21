# © Copyright 2026 Aaron Kimball
"""Tests for klorb.api_provider."""

import pytest

from klorb.api_provider import ApiProvider


def test_api_provider_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        ApiProvider()  # type: ignore[abstract]
