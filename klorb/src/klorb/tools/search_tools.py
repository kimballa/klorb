# © Copyright 2026 Aaron Kimball
"""A Tool that looks up another tool's full definition by exact name/alias or by keyword
search."""

import json
import logging
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from klorb.tools.exceptions import ToolCallError
from klorb.tools.tool import Tool
from klorb.tools.util import compile_queries, validate_queries

logger = logging.getLogger(__name__)


def _tool_schema(tool: Tool) -> dict[str, Any]:
    parameters = tool.parameters()
    if isinstance(parameters, type) and issubclass(parameters, BaseModel):
        return parameters.model_json_schema()
    return parameters


def _render_tool(tool: Tool) -> dict[str, Any]:
    return {"name": tool.name(), "description": tool.description(), "parameters": _tool_schema(tool)}


def _searchable_text(tool: Tool) -> str:
    return f"{tool.name()}\n{tool.description()}\n{json.dumps(_tool_schema(tool), default=str)}"


class SearchToolsTool(Tool):
    """Looks up the full definition of one or more registered tools, by exact name/alias or by
    case-insensitive keyword search."""

    def name(self) -> str:
        return "SearchTools"

    def aliases(self) -> Sequence[str]:
        return ("ToolSearch",)

    def category(self) -> str:
        return "TOOLS"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Looks up the full definition of one or more tools. If queries is just the name "
            "of a tool, returns only that tool's definition. Otherwise performs "
            "case-insensitive search against all tool definitions. Use this to look up the "
            "full schema of a tool named in additional_tools before calling it."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "One or more search strings.",
                },
            },
            "required": ["queries"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            queries = validate_queries(args["queries"])
        except KeyError:
            raise ValueError(
                "Missing required argument: 'queries'. Provide a non-empty array of search strings.")
        session = self.context.session
        registry = session.tool_registry if session is not None else None
        if registry is None:
            raise ToolCallError(
                "No ToolRegistry is available in this session for SearchTools to search.")
        logger.debug("SearchTools %r", queries)

        if len(queries) == 1:
            canonical_name = registry.resolve_name(queries[0])
            if canonical_name is not None:
                tool = registry.instantiate_tool(canonical_name)
                logger.debug("SearchTools %r resolved directly to %r", queries[0], canonical_name)
                return {"queries": queries, "match_count": 1, "results": [_render_tool(tool)]}

        compiled = compile_queries(queries, case_insensitive=True)
        results = [
            _render_tool(tool) for tool in registry.tools() if compiled.search(_searchable_text(tool))
        ]
        logger.debug("SearchTools found %d tool(s)", len(results))
        return {"queries": queries, "match_count": len(results), "results": results}

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        queries = args.get("queries", "?")
        if error is not None:
            return f"Search tools: {queries!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Search tools: {queries!r}"
        results = result.get("results", [])
        count = result.get("match_count", len(results))
        plural = "s" if count != 1 else ""
        header = f"Search tools: {queries!r} ({count} match{plural})"
        names = "\n".join(entry["name"] for entry in results)
        return f"{header}\n{names}" if names else header
