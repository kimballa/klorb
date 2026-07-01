# © Copyright 2026 Aaron Kimball
"""Abstract interface for a tool that can be exposed to a model and invoked by name."""

from abc import ABC
from abc import abstractmethod
from typing import Any

from pydantic import BaseModel

from klorb.tools.setup_context import ToolSetupContext


class Tool(ABC):
    """Base class for a tool that a model can be offered, and asked to invoke, by name.

    Every concrete `Tool` is constructed with a single `ToolSetupContext` argument, never
    tool-specific constructor arguments, so `ToolRegistry` can instantiate any `Tool`
    subclass uniformly (see `ToolRegistry.instantiate_tool`). A subclass that needs to
    configure itself (e.g. a per-call line limit) pulls the relevant setting out of
    `context` in its own `__init__`.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        self._context = context

    @property
    def context(self) -> ToolSetupContext:
        """Return the `ToolSetupContext` this tool was constructed with."""
        return self._context

    @abstractmethod
    def name(self) -> str:
        """Return the tool's name, as reported to the model."""

    @abstractmethod
    def description(self) -> str:
        """Return the tool's description, as reported to the model."""

    @abstractmethod
    def parameters(self) -> dict[str, Any] | type[BaseModel]:
        """Return a JSON schema dict, or a pydantic BaseModel class, describing this tool's arguments."""

    @abstractmethod
    def apply(self, args: dict[str, Any]) -> Any:
        """Execute the tool with the given arguments and return its result."""
