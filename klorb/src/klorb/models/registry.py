# © Copyright 2026 Aaron Kimball
"""Discovers `klorb-model` JSON files in a directory tree and indexes the `ConfiguredModel`s
they describe by name."""

import importlib.resources
from importlib.resources.abc import Traversable
from pathlib import Path

from klorb.models.configured_model import MODEL_SCHEMA_NAME, ConfiguredModel
from klorb.models.model import Model
from klorb.paths import KLORB_DATA_DIR
from klorb.schema_envelope import parse_versioned_json

MODELS_RESOURCE_PACKAGE = "klorb.resources"
MODELS_SUBDIR = "models"


class ModelRegistry:
    """Discovers `klorb-model` JSON files and exposes the `ConfiguredModel`s they describe by
    name.

    Scans two directories, in order: the packaged built-in models tree
    (`klorb.resources/models/`) and the user-writable override tree
    (`$KLORB_DATA_DIR/models/`, see `klorb.paths.KLORB_DATA_DIR`) — the same
    packaged-tier-then-user-override-tier convention
    `klorb.system_prompt.resolve_prompt_file` uses, except here both tiers are scanned
    wholesale rather than a single relative path being looked up in each: a user model JSON
    file whose `name` field matches a packaged one replaces it, and any other user model JSON
    file adds a new entry. A different pair of directories can be passed to the constructor
    (used by tests to scan fixture directories instead).
    """

    def __init__(
        self,
        packaged_models_dir: Traversable | None = None,
        user_models_dir: Path | None = None,
    ) -> None:
        self._models: dict[str, Model] = {}
        packaged_dir = packaged_models_dir or (
            importlib.resources.files(MODELS_RESOURCE_PACKAGE).joinpath(MODELS_SUBDIR)
        )
        user_dir = user_models_dir if user_models_dir is not None else (KLORB_DATA_DIR / MODELS_SUBDIR)
        self._discover_models(packaged_dir)
        self._discover_models(user_dir)

    def _discover_models(self, directory: Traversable | Path) -> None:
        """Parse every `*.json` file directly inside `directory` as a `klorb-model` document
        and register the `ConfiguredModel` it describes, keyed by the model's `name`. A file
        that fails schema validation is skipped (logged by `parse_versioned_json`) rather than
        raising, so one malformed model file doesn't prevent every other model — packaged or
        user-supplied — from loading.
        """
        if not directory.is_dir():
            return
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            if entry.is_dir() or not entry.name.endswith(".json"):
                continue
            text = entry.read_text(encoding="utf-8")
            data = parse_versioned_json(text, expected_schema_name=MODEL_SCHEMA_NAME, source=str(entry))
            if not data:
                continue
            model = ConfiguredModel(data, source=str(entry))
            self._models[model.name()] = model

    def register(self, model: Model) -> None:
        """Register `model` directly, keyed by its `name()`, without discovering it from a
        JSON file — replacing any existing model of the same name, the same override
        semantics the user-tier directory scan already gives over the packaged tier. Used by
        tests to register `Model` test doubles without needing real `klorb-model` JSON
        fixture files on disk.
        """
        self._models[model.name()] = model

    def get(self, name: str) -> Model:
        """Return the registered model with the given name, raising KeyError if absent."""
        return self._models[name]

    def models(self) -> list[Model]:
        """Return all registered models."""
        return list(self._models.values())

    def find_by_capability(self, capability: str) -> Model | None:
        """Return the first registered model (by name, for a deterministic pick when more than
        one qualifies) whose `Model.klorb_capabilities()` reports `capability` truthily, or
        `None` if none does.

        Lets code that picks a model programmatically (e.g.
        `klorb.permissions.risk_classifier`'s default bash-risk-classifier model) select one by
        what it's good at rather than a hardcoded model name — see `Model.klorb_capabilities`.
        """
        for name in sorted(self._models):
            if self._models[name].klorb_capabilities().get(capability):
                return self._models[name]
        return None
