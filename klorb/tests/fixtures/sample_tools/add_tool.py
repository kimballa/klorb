# © Copyright 2026 Aaron Kimball
"""A trivial Tool, with a pydantic parameter schema, used to test ToolRegistry discovery."""

from typing import Any

from pydantic import BaseModel, Field

from klorb.tools.tool import Tool


class AddArgs(BaseModel):
    """Arguments for AddTool."""

    a: int = Field(description="The first addend.")
    b: int = Field(description="The second addend.")


class AddTool(Tool):
    """Adds two integers together. Used only in tests."""

    def name(self) -> str:
        return "add"

    def description(self) -> str:
        return "Adds two integers together."

    def parameters(self) -> type[BaseModel]:
        return AddArgs

    def apply(self, args: dict[str, Any]) -> Any:
        parsed = AddArgs.model_validate(args)
        return parsed.a + parsed.b
