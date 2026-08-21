# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.wake_up_timer."""

from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.hooks.config import TimerEventConfig
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.wake_up_timer import WakeUpTimerTool
from klorb.workspace import Workspace


def _context(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> tuple[ToolSetupContext, Session]:
    process_config = ProcessConfig()
    session_config = make_session_config(
        workspace=Workspace(path=tmp_path, trusted=True))
    session = Session(session_config, provider=MagicMock(), process_config=process_config)
    ctx = ToolSetupContext(
        process_config=process_config, session_config=session_config, session=session)
    return ctx, session


def test_apply_schedules_a_one_shot_timer(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context, session = _context(tmp_path, make_session_config)
    tool = WakeUpTimerTool(context)

    created: list[TimerEventConfig] = []

    def _capture_start(events: dict) -> None:
        for entry in events.get("Timer", []):
            created.append(entry)

    session._start_event_watchers_for = _capture_start  # type: ignore[assignment]
    try:
        result = tool.apply({"delay_seconds": 120, "self_prompt": "check the build"})
    finally:
        session.close()

    assert result["scheduled"] is True
    assert result["delay_seconds"] == 120
    assert result["prompt_preview"] == "check the build"
    assert len(created) == 1
    entry = created[0]
    assert entry.one_shot is True
    assert entry.interval_minutes == pytest.approx(2.0)
    assert entry.action.type == "chat"
    assert entry.action.prompt == "check the build"


def test_apply_rejects_delay_below_minimum(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context, session = _context(tmp_path, make_session_config)
    tool = WakeUpTimerTool(context)

    with pytest.raises(ValueError, match="between 60 and 3600"):
        tool.apply({"delay_seconds": 30, "self_prompt": "wake up"})
    session.close()


def test_apply_rejects_delay_above_maximum(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context, session = _context(tmp_path, make_session_config)
    tool = WakeUpTimerTool(context)

    with pytest.raises(ValueError, match="between 60 and 3600"):
        tool.apply({"delay_seconds": 7200, "self_prompt": "wake up"})
    session.close()


def test_apply_rejects_empty_prompt(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context, session = _context(tmp_path, make_session_config)
    tool = WakeUpTimerTool(context)

    with pytest.raises(Exception, match="at least 1 character"):
        tool.apply({"delay_seconds": 120, "self_prompt": ""})
    session.close()


def test_apply_requires_an_active_session(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    process_config = ProcessConfig()
    session_config = make_session_config(
        workspace=Workspace(path=tmp_path, trusted=True))
    context = ToolSetupContext(
        process_config=process_config, session_config=session_config, session=None)
    tool = WakeUpTimerTool(context)

    with pytest.raises(ValueError, match="active session"):
        tool.apply({"delay_seconds": 120, "self_prompt": "wake up"})


def test_tool_metadata(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context, session = _context(tmp_path, make_session_config)
    tool = WakeUpTimerTool(context)
    try:
        assert tool.name() == "WakeUpTimer"
        assert tool.category() == "SESSION"
        assert tool.is_read_only() is False
    finally:
        session.close()
