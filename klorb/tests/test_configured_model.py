# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.configured_model."""

from pathlib import Path

import pytest

from klorb.models.configured_model import ConfiguredModel


def test_name_reads_from_data() -> None:
    model = ConfiguredModel({"name": "z-ai/glm-5.2"}, source="test")

    assert model.name() == "z-ai/glm-5.2"


def test_mangled_name_is_filesystem_safe() -> None:
    model = ConfiguredModel({"name": "z-ai/glm-5.2"}, source="test")

    assert model.mangled_name() == "z-ai__glm-5.2"


def test_system_prompt_is_none_without_a_model_specific_prompt_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr("klorb.system_prompt.KLORB_CONFIG_DIR", tmp_path)
    model = ConfiguredModel({"name": "z-ai/glm-5.2"}, source="test")

    assert model.system_prompt() is None


def test_settings_defaults_to_an_empty_dict() -> None:
    model = ConfiguredModel({"name": "some/model"}, source="test")

    assert model.settings() == {}


def test_settings_reads_from_data() -> None:
    model = ConfiguredModel({"name": "some/model", "settings": {"temperature": 0.5}}, source="test")

    assert model.settings() == {"temperature": 0.5}


def test_capabilities_defaults_to_an_empty_dict() -> None:
    model = ConfiguredModel({"name": "some/model"}, source="test")

    assert model.capabilities() == {}


def test_capabilities_reads_from_data() -> None:
    model = ConfiguredModel(
        {"name": "some/model", "capabilities": {"vision": True, "max_context_window": 1000}},
        source="test")

    assert model.capabilities() == {"vision": True, "max_context_window": 1000}


def test_family_and_model_version_default_to_none() -> None:
    model = ConfiguredModel({"name": "some/model"}, source="test")

    assert model.family() is None
    assert model.model_version() is None


def test_family_and_model_version_read_from_data() -> None:
    model = ConfiguredModel(
        {"name": "anthropic/claude-sonnet-5", "family": "claude-sonnet", "model_version": "5.0"},
        source="test")

    assert model.family() == "claude-sonnet"
    assert model.model_version() == "5.0"


def test_klorb_capabilities_defaults_to_an_empty_dict() -> None:
    model = ConfiguredModel({"name": "some/model"}, source="test")

    assert model.klorb_capabilities() == {}


def test_klorb_capabilities_reads_from_data() -> None:
    model = ConfiguredModel(
        {"name": "openai/gpt-oss-120b:nitro", "klorb_capabilities": {"BASH_SAFETY_EVAL": True}},
        source="test")

    assert model.klorb_capabilities() == {"BASH_SAFETY_EVAL": True}


def test_source_returns_the_constructor_argument() -> None:
    model = ConfiguredModel({"name": "some/model"}, source="/path/to/some-model.json")

    assert model.source() == "/path/to/some-model.json"
