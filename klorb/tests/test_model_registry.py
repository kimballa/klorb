# © Copyright 2026 Aaron Kimball
"""Tests for klorb.models.registry."""

import json
from pathlib import Path

from fixtures.sample_models import NO_SUCH_DIR

from klorb.models.configured_model import ConfiguredModel
from klorb.models.registry import ModelRegistry

SAMPLE_MODELS_DIR = Path(__file__).parent / "fixtures" / "sample_models"


def test_discovers_models_in_packaged_directory() -> None:
    registry = ModelRegistry(packaged_models_dir=SAMPLE_MODELS_DIR, user_models_dir=NO_SUCH_DIR)

    names = {model.name() for model in registry.models()}

    assert "delta" in names


def test_get_returns_model_by_name() -> None:
    registry = ModelRegistry(packaged_models_dir=SAMPLE_MODELS_DIR, user_models_dir=NO_SUCH_DIR)

    delta = registry.get("delta")

    assert delta.family() == "test-family"
    assert delta.model_version() == "1.0"
    assert delta.capabilities()["vision"] is True


def test_user_models_dir_overrides_a_packaged_model_of_the_same_name(tmp_path: Path) -> None:
    user_dir = tmp_path / "models"
    user_dir.mkdir()
    (user_dir / "delta.json").write_text(json.dumps({
        "schema": {"name": "klorb-model", "version": "1.0.0"},
        "name": "delta",
        "capabilities": {"vision": False},
    }))

    registry = ModelRegistry(packaged_models_dir=SAMPLE_MODELS_DIR, user_models_dir=user_dir)

    assert registry.get("delta").capabilities()["vision"] is False


def test_user_models_dir_adds_new_models(tmp_path: Path) -> None:
    user_dir = tmp_path / "models"
    user_dir.mkdir()
    (user_dir / "zeta.json").write_text(json.dumps({
        "schema": {"name": "klorb-model", "version": "1.0.0"},
        "name": "zeta",
    }))

    registry = ModelRegistry(packaged_models_dir=SAMPLE_MODELS_DIR, user_models_dir=user_dir)

    names = {model.name() for model in registry.models()}
    assert {"delta", "zeta"} <= names


def test_a_file_with_the_wrong_schema_name_is_skipped(tmp_path: Path) -> None:
    user_dir = tmp_path / "models"
    user_dir.mkdir()
    (user_dir / "wrong-schema.json").write_text(json.dumps({
        "schema": {"name": "klorb-config", "version": "1.0.0"},
        "name": "wrong-schema",
    }))

    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=user_dir)

    assert registry.models() == []


def test_register_adds_a_model_directly() -> None:
    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=NO_SUCH_DIR)
    model = ConfiguredModel({"name": "manual"}, source="test")

    registry.register(model)

    assert registry.get("manual") is model


def test_default_registry_discovers_production_models() -> None:
    registry = ModelRegistry()

    names = {model.name() for model in registry.models()}

    assert "openai/gpt-5-nano" in names
    assert "z-ai/glm-5.2" in names
    assert "anthropic/claude-sonnet-5" in names
    assert "qwen/qwen3-coder-next" in names
    assert "moonshotai/kimi-k2.7-code" in names
    assert "openai/gpt-oss-safeguard-20b:nitro" in names


def test_find_by_capability_returns_a_model_declaring_it() -> None:
    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=NO_SUCH_DIR)
    registry.register(ConfiguredModel(
        {"name": "safety-model", "klorb_capabilities": {"BASH_SAFETY_EVAL": True}}, source="test"))
    registry.register(ConfiguredModel({"name": "plain-model"}, source="test"))

    found = registry.find_by_capability("BASH_SAFETY_EVAL")

    assert found is not None
    assert found.name() == "safety-model"


def test_find_by_capability_returns_none_when_no_model_declares_it() -> None:
    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=NO_SUCH_DIR)
    registry.register(ConfiguredModel({"name": "plain-model"}, source="test"))

    assert registry.find_by_capability("BASH_SAFETY_EVAL") is None


def test_find_by_capability_ignores_falsy_values() -> None:
    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=NO_SUCH_DIR)
    registry.register(ConfiguredModel(
        {"name": "opted-out", "klorb_capabilities": {"BASH_SAFETY_EVAL": False}}, source="test"))

    assert registry.find_by_capability("BASH_SAFETY_EVAL") is None


def test_find_by_capability_prefers_a_user_directory_model_over_a_packaged_one(
    tmp_path: Path,
) -> None:
    """A user can deterministically override which model klorb picks for a capability by
    dropping a `klorb-model` JSON file into `$KLORB_DATA_DIR/models/` — even though the
    packaged directory is scanned first, the user tier outranks it for this lookup."""
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "aaa-packaged.json").write_text(json.dumps({
        "schema": {"name": "klorb-model", "version": "1.0.0"},
        "name": "aaa-packaged",
        "klorb_capabilities": {"BASH_SAFETY_EVAL": True},
    }))
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "zzz-user.json").write_text(json.dumps({
        "schema": {"name": "klorb-model", "version": "1.0.0"},
        "name": "zzz-user",
        "klorb_capabilities": {"BASH_SAFETY_EVAL": True},
    }))

    registry = ModelRegistry(packaged_models_dir=packaged_dir, user_models_dir=user_dir)

    found = registry.find_by_capability("BASH_SAFETY_EVAL")

    assert found is not None
    assert found.name() == "zzz-user"


def test_find_by_capability_prefers_a_manually_registered_model_over_a_user_directory_one(
    tmp_path: Path,
) -> None:
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "aaa-user.json").write_text(json.dumps({
        "schema": {"name": "klorb-model", "version": "1.0.0"},
        "name": "aaa-user",
        "klorb_capabilities": {"BASH_SAFETY_EVAL": True},
    }))
    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=user_dir)
    registry.register(ConfiguredModel(
        {"name": "zzz-manual", "klorb_capabilities": {"BASH_SAFETY_EVAL": True}}, source="test"))

    found = registry.find_by_capability("BASH_SAFETY_EVAL")

    assert found is not None
    assert found.name() == "zzz-manual"


def test_find_by_capability_breaks_ties_by_name_within_the_same_source() -> None:
    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=NO_SUCH_DIR)
    registry.register(ConfiguredModel(
        {"name": "zzz", "klorb_capabilities": {"BASH_SAFETY_EVAL": True}}, source="test"))
    registry.register(ConfiguredModel(
        {"name": "aaa", "klorb_capabilities": {"BASH_SAFETY_EVAL": True}}, source="test"))

    found = registry.find_by_capability("BASH_SAFETY_EVAL")

    assert found is not None
    assert found.name() == "aaa"
