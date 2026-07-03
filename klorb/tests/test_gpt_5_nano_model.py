# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.gpt_5_nano."""

from klorb.models.gpt_5_nano import Gpt5NanoModel
from klorb.openrouter import DEFAULT_MODEL


def test_name_matches_the_default_openrouter_model() -> None:
    assert Gpt5NanoModel().name() == DEFAULT_MODEL


def test_system_prompt_is_nonempty() -> None:
    assert Gpt5NanoModel().system_prompt()


def test_settings_returns_a_dict() -> None:
    assert isinstance(Gpt5NanoModel().settings(), dict)


def test_capabilities_includes_standard_keys() -> None:
    capabilities = Gpt5NanoModel().capabilities()

    assert capabilities["vision"] is True
    assert capabilities["thinking"] is True
    assert capabilities["max_context_window"] == 400_000
