# © Copyright 2026 Aaron Kimball
"""WakeUpTimer tool: lets an agent schedule a one-shot timer event during its session."""

import logging
from typing import Any

from pydantic import BaseModel, Field

from klorb.hooks.config import HookConfig, TimerEventConfig
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)

_WAKE_UP_TIMER_MIN_SECONDS = 60
_WAKE_UP_TIMER_MAX_SECONDS = 3600


class WakeUpTimerArgs(BaseModel):
    """Arguments for the WakeUpTimer tool."""

    delay_seconds: int = Field(
        description="Delay in seconds before the timer fires. Must be between 60 and 3600 (inclusive).",
        ge=_WAKE_UP_TIMER_MIN_SECONDS,
        le=_WAKE_UP_TIMER_MAX_SECONDS,
    )
    self_prompt: str = Field(
        description="The message injected into the session when the timer fires.",
        min_length=1,
    )


class WakeUpTimerTool(Tool):
    """Schedules a one-shot timer that injects a self-chosen prompt into the session after a delay."""

    def name(self) -> str:
        return "WakeUpTimer"

    def category(self) -> str:
        return "SESSION"

    def is_read_only(self) -> bool:
        return False

    def description(self) -> str:
        return (
            "Schedules a one-shot timer that delivers a self-chosen prompt to this session "
            "after a delay. Use to wake yourself up later with a specific instruction."
        )

    def parameters(self) -> type[BaseModel]:
        return WakeUpTimerArgs

    def apply(self, args: dict[str, Any]) -> Any:
        session = self.context.session
        if session is None:
            raise ValueError("WakeUpTimer requires an active session.")

        delay = args.get("delay_seconds")
        if isinstance(delay, int) and not (
            _WAKE_UP_TIMER_MIN_SECONDS <= delay <= _WAKE_UP_TIMER_MAX_SECONDS
        ):
            raise ValueError(
                f"delay_seconds must be between {_WAKE_UP_TIMER_MIN_SECONDS} "
                f"and {_WAKE_UP_TIMER_MAX_SECONDS} (inclusive); got {delay}."
            )

        validated = WakeUpTimerArgs.model_validate(args)
        delay_seconds = validated.delay_seconds
        self_prompt = validated.self_prompt

        interval_minutes = delay_seconds / 60.0
        entry = TimerEventConfig(
            action=HookConfig(type="chat", prompt=self_prompt),
            interval_minutes=interval_minutes,
            one_shot=True,
        )

        logger.debug(
            "WakeUpTimer scheduling one-shot timer: delay=%ds, prompt=%r",
            delay_seconds, self_prompt[:80],
        )

        session._start_event_watchers_for({"Timer": [entry]})

        return {
            "scheduled": True,
            "delay_seconds": delay_seconds,
            "prompt_preview": self_prompt[:120],
        }
