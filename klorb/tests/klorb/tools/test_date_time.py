# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.date_time."""

from collections.abc import Callable
from datetime import datetime

import pytest
import pytz

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.date_time import DateTimeTool
from klorb.tools.setup_context import ToolSetupContext


def _tool(make_session_config: Callable[..., SessionConfig]) -> DateTimeTool:
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=make_session_config())
    return DateTimeTool(context)


def test_apply_without_time_zone_returns_local_time(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    result = _tool(make_session_config).apply({})

    parsed = datetime.fromisoformat(result["datetime"])
    assert parsed.tzinfo is not None
    assert abs((datetime.now().astimezone() - parsed).total_seconds()) < 60


def test_apply_with_time_zone_formats_for_that_zone(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    result = _tool(make_session_config).apply({"time_zone": "America/New_York"})

    parsed = datetime.fromisoformat(result["datetime"])
    expected_offset = pytz.timezone("America/New_York").utcoffset(parsed.replace(tzinfo=None))
    assert parsed.utcoffset() == expected_offset


def test_apply_rejects_unknown_time_zone(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    with pytest.raises(ValueError, match="Unknown time zone"):
        _tool(make_session_config).apply({"time_zone": "Not/A_Zone"})


def test_tool_metadata(make_session_config: Callable[..., SessionConfig]) -> None:
    tool = _tool(make_session_config)
    assert tool.name() == "DateTime"
    assert tool.category() == "SESSION"
    assert tool.is_read_only() is True
