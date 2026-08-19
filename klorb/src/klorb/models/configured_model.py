# © Copyright 2026 Aaron Kimball
"""A `Model` implementation driven entirely by a parsed `klorb-model` JSON document, so a new
model is registered by dropping in a JSON file rather than writing a dedicated `Model`
subclass."""

from typing import Any

from pydantic import BaseModel

from klorb.models.model import CacheMgmtStyle, Model

MODEL_SCHEMA_NAME = "klorb-model"
MODEL_SCHEMA_VERSION = "1.0.0"


class _ConfiguredModelData(BaseModel):
    """Validates the shape of a `klorb-model` JSON document's data, once
    `klorb.schema_envelope.parse_versioned_json` has stripped its `schema` envelope."""

    name: str
    family: str | None = None
    model_version: str | None = None
    release_date: str | None = None
    knowledge_cutoff: str | None = None
    settings: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    klorb_capabilities: dict[str, Any] = {}
    drop_reasoning: bool = False
    cache_mgmt_style: CacheMgmtStyle = "AUTOMATIC"


class ConfiguredModel(Model):
    """Describes a model entirely from JSON data rather than
    from a hand-written `Model` subclass.
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

    def release_date(self) -> str | None:
        return self._data.release_date

    def knowledge_cutoff(self) -> str | None:
        return self._data.knowledge_cutoff

    def klorb_capabilities(self) -> dict[str, Any]:
        return self._data.klorb_capabilities

    def drop_reasoning(self) -> bool:
        return self._data.drop_reasoning

    def cache_mgmt_style(self) -> CacheMgmtStyle:
        return self._data.cache_mgmt_style
