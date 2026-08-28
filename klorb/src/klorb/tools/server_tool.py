# © Copyright 2026 Aaron Kimball
"""Base class for a tool the model provider executes server-side, folding the result directly
into its reply, instead of one dispatched locally through `Tool.apply()`.
"""

from abc import abstractmethod
from typing import Any, Literal

from klorb.tools.tool import Tool


class ServerTool(Tool):
    """A `Tool` the provider executes server-side and folds into its reply.

    `ToolRegistry.tool_definitions()` sends `provider_definition()`'s raw dict on the wire
    instead of the usual `{"type": "function", "function": {...}}` wrapper, and the model never
    names a `ServerTool` in a `tool_calls` entry for `SessionToolExecutionMixin._run_tool_calls`
    to dispatch — so `apply()` is never invoked.
    """

    def execution_mode(self) -> Literal["local", "server"]:
        return "server"

    def parameters(self) -> dict[str, Any]:
        """Unused -- `provider_definition()` supplies this tool's wire schema instead."""
        return {}

    def apply(self, args: dict[str, Any]) -> Any:
        raise RuntimeError(
            f"{self.name()} is a ServerTool; apply() should never be invoked locally.")

    @abstractmethod
    def provider_definition(self) -> dict[str, Any]:
        """Return the raw provider-specific tool spec for the `tools` wire array."""
