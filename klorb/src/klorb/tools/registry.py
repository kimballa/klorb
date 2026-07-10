# © Copyright 2026 Aaron Kimball
"""Discovers Tool implementations in a package and indexes them by name."""

import importlib
import inspect
import logging
import pkgutil
from types import ModuleType
from typing import Any

from pydantic import BaseModel

import klorb.tools as default_tools_package
from klorb.permissions.table import PermissionOverride
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Discovers Tool subclasses defined in a package once, and acts as a factory for them
    for as long as this registry (and the `Session` it belongs to) lives.

    By default, walks the klorb.tools package itself, recursively into any subpackage (e.g.
    `klorb.tools.scratchpad`) — pass a different package to discover tools defined elsewhere
    (e.g. for testing). Discovery only collects each concrete `Tool` subclass, not an instance
    of it — the module scan (`pkgutil.walk_packages`) runs once, in `__init__`, never again for
    this registry's lifetime. Every call to
    `instantiate_tool()` (directly, or via `tools()`/`tool_definitions()`) builds a fresh
    `Tool` instance from a `ToolSetupContext` assembled from this registry's `process_config`
    and `session_config`, so a tool never carries state over between calls and always sees
    those configs' current values (`session_config` in particular is held by reference and
    mutated in place elsewhere, e.g. by the TUI command palette).
    """

    def __init__(
        self, process_config: ProcessConfig, session_config: SessionConfig,
        package: ModuleType = default_tools_package,
    ) -> None:
        self._process_config = process_config
        self._session_config = session_config
        self.session: Session | None = None
        """Back-reference to the `Session` this registry belongs to, so `ToolSetupContext`s it
        builds carry `session` for `Tool`s that use `session.tool_state` — set post-construction
        by `Session.__init__`, since a `ToolRegistry` is always built before the `Session` it's
        passed into (a plain constructor argument would be circular). `None` until that happens
        (e.g. during `_discover_tools()` below, which only needs a throwaway `Tool` instance to
        read its `name()`, never `apply()`)."""
        self._tool_classes: dict[str, type[Tool]] = {}
        self._discover_tools(package)

    def _context(self, permission_override: PermissionOverride | None = None) -> ToolSetupContext:
        return ToolSetupContext(
            process_config=self._process_config, session_config=self._session_config,
            session=self.session, permission_override=permission_override)

    def _discover_tools(self, package: ModuleType) -> None:
        logger.debug("Discovering tools in package %s", package.__name__)
        prefix = f"{package.__name__}."
        for module_info in pkgutil.walk_packages(package.__path__, prefix):
            module = importlib.import_module(module_info.name)
            for _, candidate in inspect.getmembers(module, inspect.isclass):
                if candidate is Tool or not issubclass(candidate, Tool):
                    continue
                if inspect.isabstract(candidate) or candidate.__module__ != module.__name__:
                    continue
                tool = candidate(self._context())
                logger.debug("Registered tool %r from %s", tool.name(), module.__name__)
                self._tool_classes[tool.name()] = candidate
        logger.info("Discovered %d tool(s) in %s", len(self._tool_classes), package.__name__)

    def instantiate_tool(self, name: str, *, permission_override: PermissionOverride | None = None) -> Tool:
        """Factory method: construct a fresh instance of the named tool from a `ToolSetupContext`
        built from this registry's current `process_config`/`session_config`, raising
        `KeyError` if no tool with that name was discovered. `permission_override`, if given, is
        threaded onto that `ToolSetupContext` — see `ToolSetupContext.permission_override`.
        """
        return self._tool_classes[name](self._context(permission_override=permission_override))

    def tools(self) -> list[Tool]:
        """Return a freshly-instantiated Tool for every discovered tool."""
        return [self.instantiate_tool(name) for name in self._tool_classes]

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Build the OpenAI-style tool definitions to send to the model alongside a prompt."""
        definitions: list[dict[str, Any]] = []
        for tool in self.tools():
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
