# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.laguna_m1."""

from klorb.models.laguna_m1 import LAGUNA_M1_MODEL_NAME
from klorb.models.laguna_m1 import LagunaM1Model


def test_name_matches_the_openrouter_model_slug() -> None:
    assert LagunaM1Model().name() == LAGUNA_M1_MODEL_NAME == "poolside/laguna-m.1:free"


def test_system_prompt_is_nonempty() -> None:
    assert LagunaM1Model().system_prompt()


def test_settings_returns_a_dict() -> None:
    assert isinstance(LagunaM1Model().settings(), dict)


def test_capabilities_includes_standard_keys() -> None:
    capabilities = LagunaM1Model().capabilities()

    assert capabilities["vision"] is False
    assert capabilities["thinking"] is True
    assert capabilities["thinking_budget_style"] == "effort"
    assert capabilities["max_context_window"] == 256_000
