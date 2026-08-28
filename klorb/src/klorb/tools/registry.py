# © Copyright 2026 Aaron Kimball
"""Indexes `Tool` subclasses by name and acts as a factory for them; `discover_tools` builds
one by scanning a package."""

import importlib
import inspect
import logging
import pkgutil
from types import ModuleType
from typing import Any, cast

from pydantic import BaseModel

import klorb.tools as default_tools_package
from klorb.permissions.resource import PermissionOverride
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.exceptions import NoSuchToolException
from klorb.tools.server_tool import ServerTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks.common import chainlink_available
from klorb.tools.tool import Tool

_TASKS_CATEGORY = "TASKS"

ADDITIONAL_TOOL_DESCRIPTION_LIMIT = 80
"""Max length of a tool's `description()` as summarized in `additional_tool_summaries()` --
hard-truncated, not word-wrapped, since it's only meant to help the model decide whether a
`SearchTools` lookup is worth making."""

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
        builds carry `session` for `Tool`s that use `session.tool_state`. `None` until
        `Session.__init__` sets it post-construction."""
        self._tool_classes: dict[str, type[Tool]] = dict(tool_classes)
        self._alias_map: dict[str, str] = {}
        """Maps each alias to the canonical tool name, built by scanning every registered
        tool class's `aliases()`."""
        self.extra_visible_tools: frozenset[str] = frozenset()
        """Tool names that should be included in `tool_definitions()` even though their
        `Tool.default_visible()` returns `False`."""

        self._build_alias_map()

    def _build_alias_map(self) -> None:
        """
        Populates the alias map with alias -> tool_name mappings.
        """
        self._alias_map = {}
        for tool_cls in self._tool_classes.values():
            tool = tool_cls(self._context())
            name = tool.name()
            if name in self._alias_map:
                logger.warning(
                    "Canonical tool name %r already in alias map? Overriding with named tool.",
                    name)
            # Each canonical name binds to itself.
            self._alias_map[name] = name

            for alias in (tool.aliases() or ()):
                if alias in self._alias_map:
                    logger.warning(
                        "Alias %r already maps to %r; ignoring duplicate from %r",
                        alias, self._alias_map[alias], tool.name())
                    continue
                self._alias_map[alias] = tool.name()
        if self._alias_map:
            logger.debug("Tool aliases registered: %s", self._alias_map)

    @classmethod
    def discover_tools(
        cls,
        process_config: ProcessConfig,
        session_config: SessionConfig,
        package: ModuleType = default_tools_package,
    ) -> "ToolRegistry":
        """Walk `package`'s modules once, collecting every concrete `Tool` subclass defined
        directly in one, and return a `ToolRegistry` holding the discovered classes.

        The package scan (`pkgutil.walk_packages`) runs only here, not in `__init__`, so a
        session-scoped `ToolRegistry` can be built from an already-discovered
        `dict[str, type[Tool]]` without re-scanning the package. `package` defaults to
        `klorb.tools` itself.
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

    def resolve_name(self, name: str) -> str | None:
        """Return the canonical tool name `name` refers to -- itself, if it's already a
        canonical name, or the canonical name it's an alias for -- or `None` if `name` matches
        neither. Used by `SearchToolsTool` to detect an exact name/alias query before falling
        back to a keyword search."""
        return self._alias_map.get(name)

    def instantiate_tool(self, name: str, *, permission_override: PermissionOverride | None = None) -> Tool:
        """Factory method: construct a fresh instance of the named tool from a `ToolSetupContext`
        built from this registry's current `process_config`/`session_config`, raising
        `NoSuchToolException` if no tool with that name is in this registry.

        If `name` is not a canonical tool name, it is checked against every registered tool's
        `aliases()` before raising.
        """
        canonical_name = self._alias_map.get(name, name)
        try:
            return self._tool_classes[canonical_name](
                self._context(permission_override=permission_override))
        except KeyError:
            raise NoSuchToolException(name)

    def tools(self) -> list[Tool]:
        """Return a freshly-instantiated Tool for every tool in this registry."""
        return [self.instantiate_tool(name) for name in self._tool_classes]

    def tool_classes(self) -> dict[str, type[Tool]]:
        """Return a copy of this registry's name-keyed `Tool` subclass map."""
        return dict(self._tool_classes)

    def _fully_described(self, tool: Tool) -> bool:
        """Return whether `tool`'s full schema belongs in `tool_definitions()`: both
        `default_visible()` and `default_described()` return `True`, or its name is in
        `extra_visible_tools` (which bypasses both gates at once -- the eval harness uses it to
        advertise a specific hidden or undescribed tool's full schema for one eval case)."""
        if tool.name() in self.extra_visible_tools:
            return True
        return tool.default_visible() and tool.default_described()

    def tool_definitions(self) -> list[dict[str, Any]]:
        """Build the tool definitions to send to the model alongside a prompt: the OpenAI-style
        `{"type": "function", ...}` shape for a `Tool` whose `execution_mode()` is `"local"`, or
        `ServerTool.provider_definition()`'s raw dict for one whose `execution_mode()` is
        `"server"`. Only tools `_fully_described()` returns `True` for are included -- a tool
        that's visible but not described is summarized instead in `additional_tool_summaries()`.
        """
        definitions: list[dict[str, Any]] = []
        for tool in self.tools():
            if not self._fully_described(tool):
                continue
            if tool.execution_mode() == "server":
                definitions.append(cast(ServerTool, tool).provider_definition())
                continue
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

    def additional_tool_summaries(self) -> list[dict[str, str]]:
        """Return `{"name", "description"}` for every tool that's advertised to the model by
        name only -- `default_visible()` (or in `extra_visible_tools`) but not
        `_fully_described()` -- so a `<SystemInterjection>` can tell the model these tools
        exist without paying their full schema's token cost. A tool that's neither visible nor
        in `extra_visible_tools` is omitted entirely, same as `tool_definitions()`.
        `description` is hard-truncated to `ADDITIONAL_TOOL_DESCRIPTION_LIMIT` characters.
        """
        summaries: list[dict[str, str]] = []
        for tool in self.tools():
            if self._fully_described(tool):
                continue
            if not tool.default_visible() and tool.name() not in self.extra_visible_tools:
                continue
            summaries.append({
                "name": tool.name(),
                "description": tool.description()[:ADDITIONAL_TOOL_DESCRIPTION_LIMIT],
            })
        return summaries
