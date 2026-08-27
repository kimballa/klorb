# © Copyright 2026 Aaron Kimball
"""DateTime tool: reports the current date and time in ISO 8601 format."""

from datetime import datetime
from typing import Any, cast

import pytz
from pydantic import BaseModel, Field

from klorb.tools.tool import Tool


class DateTimeArgs(BaseModel):
    """Arguments for the DateTime tool."""

    time_zone: str | None = Field(
        default=None, description="Optional time zone. Omit for local time.")


class DateTimeTool(Tool):
    """Reports the current date and time in ISO 8601 format, optionally in a given time zone."""

    def name(self) -> str:
        return "DateTime"

    def category(self) -> str:
        return "SESSION"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return "Returns the current date and time."

    def parameters(self) -> type[BaseModel]:
        return DateTimeArgs

    def apply(self, args: dict[str, Any]) -> Any:
        validated = DateTimeArgs.model_validate(args)

        if validated.time_zone:
            try:
                tz = pytz.timezone(validated.time_zone)
            except pytz.exceptions.UnknownTimeZoneError:
                raise ValueError(f"Unknown time zone: {validated.time_zone!r}")
            now = datetime.now(tz)
        else:
            now = datetime.now().astimezone()

        return {"datetime": now.isoformat()}

    def format_response(self, apply_output: Any) -> str:
        return cast(str, cast(dict, apply_output)["datetime"])

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"DateTime failed: {error}"
        return self.format_response(result)
