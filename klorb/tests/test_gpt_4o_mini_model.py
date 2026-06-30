# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.gpt_4o_mini."""

from klorb.models.gpt_4o_mini import Gpt4oMiniModel
from klorb.openrouter import DEFAULT_MODEL


def test_name_matches_the_default_openrouter_model() -> None:
    assert Gpt4oMiniModel().name() == DEFAULT_MODEL


def test_system_prompt_is_nonempty() -> None:
    assert Gpt4oMiniModel().system_prompt()


def test_settings_returns_a_dict() -> None:
    assert isinstance(Gpt4oMiniModel().settings(), dict)


def test_capabilities_includes_standard_keys() -> None:
    capabilities = Gpt4oMiniModel().capabilities()

    assert capabilities["vision"] is True
    assert capabilities["thinking"] is False
    assert capabilities["max_context_window"] == 128_000
