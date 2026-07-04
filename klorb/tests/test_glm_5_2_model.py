# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.glm_5_2."""

from pathlib import Path

import pytest

from klorb.models.glm_5_2 import GLM_5_2_MODEL_NAME
from klorb.models.glm_5_2 import Glm52Model


def test_name_matches_the_openrouter_model_slug() -> None:
    assert Glm52Model().name() == GLM_5_2_MODEL_NAME == "z-ai/glm-5.2"


def test_mangled_name_is_filesystem_safe() -> None:
    assert Glm52Model().mangled_name() == "z-ai__glm-5.2"


def test_system_prompt_is_none_without_a_model_specific_prompt_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr("klorb.system_prompts.KLORB_CONFIG_DIR", tmp_path)

    assert Glm52Model().system_prompt() is None


def test_settings_returns_a_dict() -> None:
    assert isinstance(Glm52Model().settings(), dict)


def test_capabilities_includes_standard_keys() -> None:
    capabilities = Glm52Model().capabilities()

    assert capabilities["vision"] is False
    assert capabilities["thinking"] is True
    assert capabilities["thinking_budget_style"] == "effort"
    assert capabilities["max_context_window"] == 256_000
