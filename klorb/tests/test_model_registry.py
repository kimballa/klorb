# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.registry."""

import fixtures.sample_models as sample_models_package

from klorb.models.registry import ModelRegistry


def test_discovers_models_in_package() -> None:
    registry = ModelRegistry(package=sample_models_package)

    names = {model.name() for model in registry.models()}

    assert names == {"alpha", "beta", "gamma"}


def test_get_returns_model_by_name() -> None:
    registry = ModelRegistry(package=sample_models_package)

    alpha = registry.get("alpha")

    assert alpha.system_prompt() == "You are Alpha."
    assert alpha.capabilities()["vision"] is True


def test_default_registry_discovers_production_models() -> None:
    registry = ModelRegistry()

    names = {model.name() for model in registry.models()}

    assert "openai/gpt-5-nano" in names
