# © Copyright 2026 Aaron Kimball
"""Discovers Model implementations in a package and indexes them by name."""

import importlib
import inspect
import pkgutil
from types import ModuleType

import klorb.models as default_models_package
from klorb.models.model import Model


class ModelRegistry:
    """Discovers Model subclasses defined in a package and exposes them by name.

    By default, walks the klorb.models package itself; pass a different package to
    discover models defined elsewhere (e.g. for testing).
    """

    def __init__(self, package: ModuleType = default_models_package) -> None:
        self._models: dict[str, Model] = {}
        self._discover_models(package)

    def _discover_models(self, package: ModuleType) -> None:
        prefix = f"{package.__name__}."
        for module_info in pkgutil.iter_modules(package.__path__, prefix):
            module = importlib.import_module(module_info.name)
            for _, candidate in inspect.getmembers(module, inspect.isclass):
                if candidate is Model or not issubclass(candidate, Model):
                    continue
                if inspect.isabstract(candidate) or candidate.__module__ != module.__name__:
                    continue
                model = candidate()
                self._models[model.name()] = model

    def get(self, name: str) -> Model:
        """Return the registered model with the given name, raising KeyError if absent."""
        return self._models[name]

    def models(self) -> list[Model]:
        """Return all registered models."""
        return list(self._models.values())
