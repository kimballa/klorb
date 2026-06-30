# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.model."""

import pytest

from klorb.models.model import Model


def test_model_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Model()  # type: ignore[abstract]
