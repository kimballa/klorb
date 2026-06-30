# © Copyright 2026 Aaron Kimball
"""Discovers Tool implementations in a package and indexes them by name."""

import importlib
import inspect
import pkgutil
from types import ModuleType
from typing import Any

from pydantic import BaseModel

import klorb.tools as default_tools_package
from klorb.tools.tool import Tool


class ToolRegistry:
    """Discovers Tool subclasses defined in a package and exposes them by name.

    By default, walks the klorb.tools package itself; pass a different package to
    discover tools defined elsewhere (e.g. for testing).
    """

    def __init__(self, package: ModuleType = default_tools_package) -> None:
        self._tools: dict[str, Tool] = {}
        self._discover_tools(package)

    def _discover_tools(self, package: ModuleType) -> None:
        prefix = f"{package.__name__}."
        for module_info in pkgutil.iter_modules(package.__path__, prefix):
            module = importlib.import_module(module_info.name)
            for _, candidate in inspect.getmembers(module, inspect.isclass):
                if candidate is Tool or not issubclass(candidate, Tool):
                    continue
                if inspect.isabstract(candidate) or candidate.__module__ != module.__name__:
                    continue
                tool = candidate()
                self._tools[tool.name()] = tool

    def get(self, name: str) -> Tool:
        """Return the registered tool with the given name, raising KeyError if absent."""
        return self._tools[name]

    def tools(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Build the OpenAI-style tool definitions to send to the model alongside a prompt."""
        definitions: list[dict[str, Any]] = []
        for tool in self._tools.values():
            parameters = tool.parameters()
            if isinstance(parameters, type) and issubclass(parameters, BaseModel):
                schema = parameters.model_json_schema()
            else:
                schema = parameters
            definitions.append({
                "type": "function",
                "function": {
                    "name": tool.name(),
                    "description": tool.description(),
                    "parameters": schema,
                },
            })
        return definitions
