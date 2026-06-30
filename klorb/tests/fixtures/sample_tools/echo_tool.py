# © Copyright 2026 Aaron Kimball
"""A trivial Tool, with a raw JSON schema, used to test ToolRegistry discovery."""

from typing import Any

from klorb.tools.tool import Tool


class EchoTool(Tool):
    """Echoes back the given message. Used only in tests."""

    def name(self) -> str:
        return "echo"

    def description(self) -> str:
        return "Echoes back the given message."

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
