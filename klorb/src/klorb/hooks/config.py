# © Copyright 2026 Aaron Kimball
"""Pydantic models for the `hooks`/`events` process-config keys: the handler types a
`klorb-config.json` layer can declare (`bash`/`classifier`/`chat`), the filter clauses that
gate whether a handler is eligible to run, and the event-specific config shapes
(`FileSystemModified`, `Timer`, `WorkspaceTrustChanged`).
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HOOK_NAMES: frozenset[str] = frozenset({
    "onProcessStart", "onSessionStart", "onSubmitUserPrompt", "onRequestPermission",
    "onToolUse", "onToolResult", "onActivateSkill", "onSubagentStart", "onSubagentTurnEnd",
    "onAgentTurnEnd", "onSessionEnd", "onProcessEnd",
})
"""Every lifecycle moment a `hooks` config key may name."""

EVENT_NAMES: frozenset[str] = frozenset({"FileSystemModified", "Timer", "WorkspaceTrustChanged"})
"""Every event kind an `events` config key may name."""

RESET_SESSION_CAPABLE_HOOKS: frozenset[str] = frozenset({
    "onAgentTurnEnd", "FileSystemModified", "Timer", "WorkspaceTrustChanged",
})
"""Every hook/event name whose `HookOutput.reset_session` `HookDispatcher` honors."""

MIN_EVENT_DEBOUNCE_SECONDS: float = 10.0
"""The default debounce window `FileSystemModified`'s watcher waits after the most recent
change before delivering a batch, and the floor `Timer`'s own interval/cron scheduling may not
go tighter."""

HookHandlerType = Literal["bash", "classifier", "chat"]

HookFilterSubjectField = Literal["reason", "message", "tool_name", "tool_result", "skill_name"]

HOOK_FILTER_SUBJECT_FIELDS: dict[str, HookFilterSubjectField] = {
    "onProcessStart": "reason",
    "onSessionStart": "reason",
    "onSubmitUserPrompt": "message",
    "onRequestPermission": "reason",
    "onToolUse": "tool_name",
    "onToolResult": "tool_result",
    "onActivateSkill": "skill_name",
    "onSubagentStart": "message",
    "onSubagentTurnEnd": "message",
    "onAgentTurnEnd": "message",
    "onSessionEnd": "reason",
    "onProcessEnd": "reason",
}
"""Which `HookInput` field a `HookConfig.filter` is evaluated against, per hook name: a
process/session start/end hook filters on `reason`, a hook centered on a chunk of conversation
text (a user prompt, an agent reply, a subagent's output) filters on `message`, `onToolUse`
filters on `tool_name`, `onToolResult` filters on `tool_result`, and `onActivateSkill` filters on
`skill_name` (the skill's bare, canonical name). `onRequestPermission`'s own subject is left as
`"reason"` (a placeholder default): its `HookInput` shape is deferred future work, not yet
documented."""


class HookConfigFilter(BaseModel):
    """One filter clause gating whether a `HookConfig`/event `action` is eligible to run.
    Each present field must hold for the filter to pass; `any`/`all` recurse into nested
    filters and combine them with OR/AND, `not_` negates a nested filter.
    """

    model_config = ConfigDict(populate_by_name=True)

    matches: str | None = None
    pattern: str | None = None
    contains: str | None = None
    any: "list[HookConfigFilter] | None" = None
    all: "list[HookConfigFilter] | None" = None
    not_: "HookConfigFilter | None" = Field(default=None, alias="not")

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, value: str | None) -> str | None:
        """Reject invalid regex patterns at config load time so a malformed `pattern` can't
        crash the dispatch loop later (where `re.search` would raise `re.error`)."""
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex pattern {value!r}: {exc}") from exc
        return value


class HookConfig(BaseModel):
    """One handler entry in a `hooks` config list (or an event's `action`): `type` selects how
    it runs (`bash` via `shell`/`command`, `classifier`/`chat` via `prompt`), `name` tells it
    apart from other entries in the same list, and `filter` gates whether it's eligible.
    """

    type: HookHandlerType
    shell: str | None = None
    command: list[str] | None = None
    prompt: str | None = None
    name: str | None = None
    filter: HookConfigFilter | None = None


class EventConfig(BaseModel):
    """Base shape for every event-specific config below: the `action` (a `HookConfig`)
    run when the event fires."""

    action: HookConfig


class FileSystemModifiedEventConfig(EventConfig):
    """One `events.FileSystemModified` entry: `watch` names a workspace-relative file or
    directory (a directory is watched recursively) whose changes trigger `action`."""

    watch: str


class TimerEventConfig(EventConfig):
    """One `events.Timer` entry: fires `action` either every `interval_minutes` or on
    `cron`'s schedule."""

    interval_minutes: float | None = None
    cron: str | None = None


class WorkspaceTrustChangedEventConfig(EventConfig):
    """One `events.WorkspaceTrustChanged` entry: runs `action` whenever a workspace's trust
    decision changes against an already-live root session. Has no selector field of its own."""


EVENT_CONFIG_MODELS: dict[str, type[EventConfig]] = {
    "FileSystemModified": FileSystemModifiedEventConfig,
    "Timer": TimerEventConfig,
    "WorkspaceTrustChanged": WorkspaceTrustChangedEventConfig,
}
"""Maps each `EVENT_NAMES` entry to the `EventConfig` subclass its handler-list entries are
parsed as."""
