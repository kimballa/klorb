# © Copyright 2026 Aaron Kimball
"""A trivial Tool whose `update_args()` drops a "big" argument once the call succeeds, used to
test `Session._run_tool_calls()`'s `reflected_tool_args` wiring. Used only in tests."""

from typing import Any

from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.tool import Tool


class CompactingTool(Tool):
    """Echoes back `keep`, ignoring `big`. Used only in tests."""

    def name(self) -> str:
        return "compacting"

    def description(self) -> str:
        return "Echoes back keep, ignoring big."

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "keep": {"type": "string", "description": "Kept in the reflected args."},
                "big": {"type": "string", "description": "Dropped from the reflected args."},
            },
            "required": ["keep", "big"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        return args["keep"]

    def update_args(
        self, tool_args: dict[str, Any], tool_response: Any, err_info: ToolCallErrorInfo,
    ) -> dict[str, Any]:
        if err_info.is_error:
            return tool_args
        return {k: v for k, v in tool_args.items() if k != "big"}
