# © Copyright 2026 Aaron Kimball
"""Abstract interface for a tool that can be exposed to a model and invoked by name."""

from abc import ABC
from abc import abstractmethod
from typing import Any

from pydantic import BaseModel


class Tool(ABC):
    """Base class for a tool that a model can be offered, and asked to invoke, by name."""

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
