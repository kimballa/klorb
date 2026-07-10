# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.claude_sonnet_5."""

from pathlib import Path

import pytest

from klorb.models.claude_sonnet_5 import CLAUDE_SONNET_5_MODEL_NAME, ClaudeSonnet5Model


def test_name_matches_the_openrouter_model_slug() -> None:
    assert ClaudeSonnet5Model().name() == CLAUDE_SONNET_5_MODEL_NAME == "anthropic/claude-sonnet-5"


def test_mangled_name_is_filesystem_safe() -> None:
    assert ClaudeSonnet5Model().mangled_name() == "anthropic__claude-sonnet-5"


def test_system_prompt_is_none_without_a_model_specific_prompt_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr("klorb.system_prompts.KLORB_CONFIG_DIR", tmp_path)

    assert ClaudeSonnet5Model().system_prompt() is None


def test_settings_returns_a_dict() -> None:
    assert isinstance(ClaudeSonnet5Model().settings(), dict)


def test_capabilities_includes_standard_keys() -> None:
    capabilities = ClaudeSonnet5Model().capabilities()

    assert capabilities["vision"] is True
    assert capabilities["thinking"] is True
    assert capabilities["thinking_budget_style"] == "effort"
    assert capabilities["max_context_window"] == 1_000_000
