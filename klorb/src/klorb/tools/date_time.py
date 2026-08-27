# © Copyright 2026 Aaron Kimball
"""DateTime tool: reports the current date and time in ISO 8601 format."""

from datetime import datetime
from typing import Any

import pytz
from pydantic import BaseModel, Field

from klorb.tools.tool import Tool


class DateTimeArgs(BaseModel):
    """Arguments for the DateTime tool."""

    time_zone: str | None = Field(
        default=None,
        description=(
            "IANA time zone name (e.g. 'America/New_York', 'UTC') to format the current time "
            "in. Omit to use local system time."
        ),
    )


class DateTimeTool(Tool):
    """Reports the current date and time in ISO 8601 format, optionally in a given time zone."""

    def name(self) -> str:
        return "DateTime"

    def category(self) -> str:
        return "SESSION"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Returns the current date and time as ISO 8601 text. Accepts an optional "
            "time_zone (any name pytz recognizes); without it, returns local system time."
        )

    def parameters(self) -> type[BaseModel]:
        return DateTimeArgs

    def apply(self, args: dict[str, Any]) -> Any:
        validated = DateTimeArgs.model_validate(args)

        if validated.time_zone is not None:
            try:
                tz = pytz.timezone(validated.time_zone)
            except pytz.exceptions.UnknownTimeZoneError:
                raise ValueError(f"Unknown time zone: {validated.time_zone!r}")
            now = datetime.now(tz)
        else:
            now = datetime.now().astimezone()

        return {"datetime": now.isoformat()}
