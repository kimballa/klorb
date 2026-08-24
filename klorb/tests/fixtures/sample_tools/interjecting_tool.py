# © Copyright 2026 Aaron Kimball
"""A trivial Tool whose `call_interjection()` always fires, used to test
`Session._run_tool_calls()`'s per-call `system_interjections` wiring. Used only in tests."""

from typing import Any

from klorb.tools.tool import Tool


class InterjectingTool(Tool):
    """Echoes back `message`, always attaching a fixed interjection. Used only in tests."""

    def name(self) -> str:
        return "interjecting"

    def description(self) -> str:
        return "Echoes back message, always attaching a fixed interjection."

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "The message to echo back."},
            },
            "required": ["message"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        return args["message"]

    def call_interjection(self, result: Any) -> str | None:
        return f"heads up: {result}"
