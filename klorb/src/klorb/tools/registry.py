# © Copyright 2026 Aaron Kimball
"""Indexes `Tool` subclasses by name and acts as a factory for them; `discover_tools` builds
one by scanning a package."""

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
from klorb.tools.exceptions import NoSuchToolException
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks.common import chainlink_available
from klorb.tools.tool import Tool

_TASKS_CATEGORY = "TASKS"

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Holds a name-keyed set of `Tool` subclasses and acts as a factory for them for as
    long as this registry (and the `Session` it belongs to) lives.

    A registry is built in one of two ways:

    * `ToolRegistry.discover_tools(process_config, session_config, package=...)` walks a
      package's modules once (the `klorb.tools` package by default, recursively into any
      subpackage like `klorb.tools.scratchpad`) and returns a registry holding every
      concrete `Tool` subclass defined directly in one. This is the bootstrap path: the
      full set of tools the harness offers, discovered once so the import/scan work isn't
      repeated.
    * `ToolRegistry(process_config, session_config, tool_classes)` constructs a registry
      directly from an already-discovered `dict[str, type[Tool]]` (cloned, not held by
      reference), so a session-scoped registry can be built from a subset of a bootstrap
      registry's classes without re-scanning any package.

    Discovery only collects each concrete `Tool` subclass, not an instance of it. Every
    call to `instantiate_tool()` (directly, or via `tools()`/`tool_definitions()`) builds a
    fresh `Tool` instance from a `ToolSetupContext` assembled from this registry's
    `process_config` and `session_config`, so a tool never carries state over between calls
    and always sees those configs' current values (`session_config` in particular is held
    by reference and mutated in place elsewhere, e.g. by the TUI command palette).
    """

    def __init__(
        self, process_config: ProcessConfig, session_config: SessionConfig,
        tool_classes: dict[str, type[Tool]],
    ) -> None:
        self._process_config = process_config
        self._session_config = session_config
        self.session: Session | None = None
        """Back-reference to the `Session` this registry belongs to, so `ToolSetupContext`s it
        builds carry `session` for `Tool`s that use `session.tool_state` — set post-construction
        by `Session.__init__`, since a `ToolRegistry` is always built before the `Session` it's
        passed into (a plain constructor argument would be circular). `None` until that
        happens, and `None` for a registry built by `discover_tools` before one is passed in
        (discovery only needs a throwaway `Tool` instance to read its `name()`, never
        `apply()`)."""
        self._tool_classes: dict[str, type[Tool]] = dict(tool_classes)

    @classmethod
    def discover_tools(
        cls,
        process_config: ProcessConfig,
        session_config: SessionConfig,
        package: ModuleType = default_tools_package,
    ) -> "ToolRegistry":
        """Walk `package`'s modules once, collecting every concrete `Tool` subclass defined
        directly in one, and return a `ToolRegistry` holding the discovered classes — the
        bootstrap path that produces a registry of all tools the harness offers.

        The package scan (`pkgutil.walk_packages`) runs only here, not in `__init__`, so a
        session-scoped `ToolRegistry` can be built from an already-discovered
        `dict[str, type[Tool]]` (e.g. a filtered subset of this registry's classes) without
        re-scanning the package. `package` defaults to `klorb.tools` itself; pass a different
        package to discover tools defined elsewhere (e.g. a test fixture package).
        """
        logger.debug("Discovering tools in package %s", package.__name__)
        tool_classes: dict[str, type[Tool]] = {}
        context = ToolSetupContext(
            process_config=process_config, session_config=session_config, session=None)
        prefix = f"{package.__name__}."
        for module_info in pkgutil.walk_packages(package.__path__, prefix):
            module = importlib.import_module(module_info.name)
            for _, candidate in inspect.getmembers(module, inspect.isclass):
                if candidate is Tool or not issubclass(candidate, Tool):
                    continue
                if inspect.isabstract(candidate) or candidate.__module__ != module.__name__:
                    continue
                tool = candidate(context)
                try:
                    category = tool.category()
                except NotImplementedError:
                    # Tools in a non-production package (e.g. a test fixture package) aren't
                    # held to the "every tool overrides category()" contract production tools
                    # are -- see test_tool_registry.py's
                    # test_every_production_tool_overrides_category_and_is_read_only.
                    category = None
                if category == _TASKS_CATEGORY and not chainlink_available():
                    # No Todo* tool call can ever be attempted in this session -- see
                    # docs/specs/chainlink-task-tracking.md's "Setup" section.
                    logger.debug("Skipping %r: chainlink binary not found.", tool.name())
                    continue
                logger.debug("Registered tool %r from %s", tool.name(), module.__name__)
                tool_classes[tool.name()] = candidate
        logger.info("Discovered %d tool(s) in %s", len(tool_classes), package.__name__)
        return cls(process_config, session_config, tool_classes)

    def _context(self, permission_override: PermissionOverride | None = None) -> ToolSetupContext:
        return ToolSetupContext(
            process_config=self._process_config, session_config=self._session_config,
            session=self.session, permission_override=permission_override)

    def instantiate_tool(self, name: str, *, permission_override: PermissionOverride | None = None) -> Tool:
        """Factory method: construct a fresh instance of the named tool from a `ToolSetupContext`
        built from this registry's current `process_config`/`session_config`, raising
        `NoSuchToolException` if no tool with that name is in this registry.
        `permission_override`, if given, is threaded onto that `ToolSetupContext` — see
        `ToolSetupContext.permission_override`.
        """
        try:
            return self._tool_classes[name](self._context(permission_override=permission_override))
        except KeyError:
            raise NoSuchToolException(name)

    def tools(self) -> list[Tool]:
        """Return a freshly-instantiated Tool for every tool in this registry."""
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
