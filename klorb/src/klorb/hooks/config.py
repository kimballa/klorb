# © Copyright 2026 Aaron Kimball
"""Pydantic models for the `hooks`/`events` process-config keys: the handler types a
`klorb-config.json` layer can declare (`bash`/`classifier`/`chat`), the filter clauses that
gate whether a handler is eligible to run, and the event-specific config shapes
(`FileSystemModified`, `Timer`, `WorkspaceTrustChanged`).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HOOK_NAMES: frozenset[str] = frozenset({
    "onProcessStart", "onSessionStart", "onSubmitUserPrompt", "onRequestPermission",
    "onToolUse", "onToolResult", "onSubagentStart", "onSubagentTurnEnd", "onAgentTurnEnd",
    "onSessionEnd", "onProcessEnd",
})
"""Every lifecycle moment a `hooks` config key may name."""

EVENT_NAMES: frozenset[str] = frozenset({"FileSystemModified", "Timer", "WorkspaceTrustChanged"})
"""Every event kind an `events` config key may name."""

HookHandlerType = Literal["bash", "classifier", "chat"]


class HookConfigFilter(BaseModel):
    """One filter clause gating whether a `HookConfig`/event `action` is eligible to run.
    Each present field must hold for the filter to pass; `any`/`all` recurse into nested
    filters and combine them with OR/AND, `not_` negates a nested filter. See
    `klorb.hooks.filters.evaluate_filter` for the pure function that interprets an instance of
    this model against a subject string.
    """

    model_config = ConfigDict(populate_by_name=True)

    matches: str | None = None
    pattern: str | None = None
    contains: str | None = None
    any: "list[HookConfigFilter] | None" = None
    all: "list[HookConfigFilter] | None" = None
    not_: "HookConfigFilter | None" = Field(default=None, alias="not")


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
    """Base shape shared by every event-specific config below: the `action` (a `HookConfig`)
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
    decision changes against an already-live root session. Has no selector field of its own —
    every entry in the list runs whenever the event fires."""


EVENT_CONFIG_MODELS: dict[str, type[EventConfig]] = {
    "FileSystemModified": FileSystemModifiedEventConfig,
    "Timer": TimerEventConfig,
    "WorkspaceTrustChanged": WorkspaceTrustChangedEventConfig,
}
"""Maps each `EVENT_NAMES` entry to the `EventConfig` subclass its handler-list entries are
parsed as."""
