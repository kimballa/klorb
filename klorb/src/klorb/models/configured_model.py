# © Copyright 2026 Aaron Kimball
"""A `Model` implementation driven entirely by a parsed `klorb-model` JSON document (see
docs/specs/model-framework.md), so a new model is registered by dropping in a JSON file
rather than writing a dedicated `Model` subclass — see
docs/adrs/back-models-with-json-resource-files-not-python-classes.md."""

from typing import Any

from pydantic import BaseModel

from klorb.models.model import Model

MODEL_SCHEMA_NAME = "klorb-model"
MODEL_SCHEMA_VERSION = "1.0.0"


class _ConfiguredModelData(BaseModel):
    """Validates the shape of a `klorb-model` JSON document's data, once
    `klorb.schema_envelope.parse_versioned_json` has stripped its `schema` envelope."""

    name: str
    family: str | None = None
    model_version: str | None = None
    settings: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    klorb_capabilities: dict[str, Any] = {}
    drop_reasoning: bool = False


class ConfiguredModel(Model):
    """Describes a model entirely from JSON data (see `_ConfiguredModelData`) rather than
    from a hand-written `Model` subclass. `klorb.models.registry.ModelRegistry` constructs one
    per `klorb-model` JSON file it discovers, passing the file's already-parsed,
    envelope-stripped data plus a human-readable `source` (the file's path or packaged
    resource name) used only for error messages and debugging.
    """

    def __init__(self, data: dict[str, Any], *, source: str) -> None:
        self._source = source
        self._data = _ConfiguredModelData.model_validate(data)

    def source(self) -> str:
        """Return the file path or packaged resource name this model's data was loaded
        from, for error messages and debugging."""
        return self._source

    def name(self) -> str:
        return self._data.name

    def settings(self) -> dict[str, Any]:
        return self._data.settings

    def capabilities(self) -> dict[str, Any]:
        return self._data.capabilities

    def family(self) -> str | None:
        return self._data.family

    def model_version(self) -> str | None:
        return self._data.model_version

    def klorb_capabilities(self) -> dict[str, Any]:
        return self._data.klorb_capabilities

    def drop_reasoning(self) -> bool:
        return self._data.drop_reasoning
