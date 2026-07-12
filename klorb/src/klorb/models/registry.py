# © Copyright 2026 Aaron Kimball
"""Discovers `klorb-model` JSON files in a directory tree and indexes the `ConfiguredModel`s
they describe by name."""

import importlib.resources
from enum import IntEnum
from importlib.resources.abc import Traversable
from pathlib import Path

from klorb.models.configured_model import MODEL_SCHEMA_NAME, ConfiguredModel
from klorb.models.model import Model
from klorb.paths import KLORB_DATA_DIR
from klorb.schema_envelope import parse_versioned_json

MODELS_RESOURCE_PACKAGE = "klorb.resources"
MODELS_SUBDIR = "models"


class _ModelSource(IntEnum):
    """Priority tier a registered model came from — lower value wins when `find_by_capability`
    has more than one qualifying model to choose from. Unrelated to `_discover_models`' own
    packaged-then-user scan order, which governs same-`name` override instead (see
    `ModelRegistry`'s docstring)."""

    MANUAL = 0
    """Registered directly via `register()` — the most explicit source, so it outranks
    anything discovered from a file."""
    USER = 1
    """Discovered from `$KLORB_DATA_DIR/models/`. Ranked above `PACKAGED` so a user can add a
    model that deterministically wins a `find_by_capability` lookup — e.g. declaring
    `klorb_capabilities.BASH_SAFETY_EVAL` on a model of their own choosing — without needing
    to know or replace the packaged model that would otherwise be picked."""
    PACKAGED = 2
    """Discovered from the packaged `klorb.resources/models/` tree — the built-in models
    klorb ships with."""


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

    Separately from that scan order, each registered model is tagged with a `_ModelSource`
    priority tier (`register()` > user-directory > packaged-directory) that `find_by_capability`
    consults when more than one registered model declares the same capability — see that
    method and `_ModelSource` for why this is a distinct notion from scan order.
    """

    def __init__(
        self,
        packaged_models_dir: Traversable | None = None,
        user_models_dir: Path | None = None,
    ) -> None:
        self._models: dict[str, Model] = {}
        self._sources: dict[str, _ModelSource] = {}
        packaged_dir = packaged_models_dir or (
            importlib.resources.files(MODELS_RESOURCE_PACKAGE).joinpath(MODELS_SUBDIR)
        )
        user_dir = user_models_dir if user_models_dir is not None else (KLORB_DATA_DIR / MODELS_SUBDIR)
        self._discover_models(packaged_dir, source=_ModelSource.PACKAGED)
        self._discover_models(user_dir, source=_ModelSource.USER)

    def _discover_models(self, directory: Traversable | Path, *, source: "_ModelSource") -> None:
        """Parse every `*.json` file directly inside `directory` as a `klorb-model` document
        and register the `ConfiguredModel` it describes, keyed by the model's `name` and tagged
        with `source`. A file that fails schema validation is skipped (logged by
        `parse_versioned_json`) rather than raising, so one malformed model file doesn't
        prevent every other model — packaged or user-supplied — from loading.
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
            self._sources[model.name()] = source

    def register(self, model: Model) -> None:
        """Register `model` directly, keyed by its `name()`, without discovering it from a
        JSON file — replacing any existing model of the same name, the same override
        semantics the user-tier directory scan already gives over the packaged tier, and
        tagged with the highest `find_by_capability` priority (`_ModelSource.MANUAL`). Used by
        tests to register `Model` test doubles without needing real `klorb-model` JSON
        fixture files on disk.
        """
        self._models[model.name()] = model
        self._sources[model.name()] = _ModelSource.MANUAL

    def get(self, name: str) -> Model:
        """Return the registered model with the given name, raising KeyError if absent."""
        return self._models[name]

    def models(self) -> list[Model]:
        """Return all registered models."""
        return list(self._models.values())

    def find_by_capability(self, capability: str) -> Model | None:
        """Return the registered model that best qualifies for `capability` — the one whose
        `Model.klorb_capabilities()` reports it truthily, preferring a higher-priority
        `_ModelSource` (manually `register()`-ed, then user-directory, then packaged) and
        breaking ties by name for a deterministic pick among equals. Returns `None` if no
        registered model declares the capability.

        A user-added model (in `$KLORB_DATA_DIR/models/`) always outranks a packaged one here,
        even though the packaged tier is scanned first — this lets a user deterministically
        override which model klorb picks for a capability (e.g. `"BASH_SAFETY_EVAL"`) just by
        dropping in a `klorb-model` JSON file that declares it, without needing to know about
        or replace whichever packaged model would otherwise win. See `Model.klorb_capabilities`.
        """
        candidates = [
            name for name, model in self._models.items() if model.klorb_capabilities().get(capability)
        ]
        if not candidates:
            return None
        best = min(candidates, key=lambda name: (self._sources[name], name))
        return self._models[best]
