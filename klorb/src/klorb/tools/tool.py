# © Copyright 2026 Aaron Kimball
"""Abstract interface for a tool that can be exposed to a model and invoked by name."""

import json
from abc import ABC
from abc import abstractmethod
from typing import Any

from pydantic import BaseModel

from klorb.tools.setup_context import ToolSetupContext


def default_tool_call_summary(name: str, args: dict[str, Any], error: str | None) -> str:
    """`Tool.summary()`'s default body, and what a caller renders for a tool call whose name
    isn't recognized by a `ToolRegistry` (so there's no `Tool` instance to call `.summary()`
    on): the tool's name, plus `error` if the call failed.
    """
    return f"{name}: {error}" if error is not None else name


def default_tool_call_detail(
    name: str, args: dict[str, Any], result: Any, error: str | None,
) -> str:
    """`Tool.detail_view()`'s default body, and what a caller renders for a tool call whose
    name isn't recognized by a `ToolRegistry`: pretty-printed JSON of `args` alongside `result`
    (on success) or `error` (on failure). `name` is accepted for symmetry with
    `default_tool_call_summary()` but isn't part of the rendered JSON.
    """
    payload: dict[str, Any] = {"args": args}
    payload["error" if error is not None else "result"] = error if error is not None else result
    return json.dumps(payload, indent=2, default=str)


def truncate_lines(text: str, max_lines: int) -> str:
    """Return `text` unchanged if it has at most `max_lines` lines, otherwise its first
    `max_lines` lines followed by a trailing `"..."` line — used by a `detail_view()` override
    to cap a long multi-line field (e.g. a file's contents) instead of dumping it in full.
    """
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + "\n..."


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

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Return a one-line, human-friendly description of one call to this tool, shown by
        default wherever a UI renders tool call activity (see docs/specs/terminal-repl.md).

        `error is None` means the call succeeded (even if `result` happens to be `None` too);
        `error is not None` means it failed and `result` is meaningless — this is the sole
        success/failure discriminant. Defaults to `default_tool_call_summary()`; override for a
        tool-specific line, e.g. `"Edit file: foo.py (+15/-6)"`.
        """
        return default_tool_call_summary(self.name(), args, error)

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Return a fuller, still human-friendly (not raw JSON, where avoidable) rendering of
        this call's arguments and result/error, shown when a UI's user asks for more detail
        than `summary()` gives.

        Same `error`-is-the-success-discriminant contract as `summary()`. Defaults to
        `default_tool_call_detail()` (pretty-printed JSON); override only when that's a poor
        fit, e.g. to truncate a long field instead of dumping it in full.
        """
        return default_tool_call_detail(self.name(), args, result, error)
