# © Copyright 2026 Aaron Kimball
"""The JSON schema hook/event dispatch feeds to a `bash` subprocess's stdin (or hands to a
`classifier`/`chat` handler): `HookInput`/`EventInput` describe the lifecycle moment or
occurrence that triggered the handler, `HookOutput` describes what the handler decided.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from klorb.permissions.table import Verdict


class HookInput(BaseModel):
    """What a hook handler receives describing the lifecycle moment that triggered it."""

    hook: str
    name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    workspace_root: str
    reason: str | None = None
    """Why `hook` fired, for a hook whose `HOOK_FILTER_SUBJECT_FIELDS` subject is `"reason"`."""
    message: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None
    """`onToolResult` only: the tool call's own substantive result content. Never includes
    `system_interjections`; a hook can't see or override those."""
    skill_name: str | None = None
    skill_namespace: str | None = None
    is_user_mentioned: bool | None = None
    """Set only for `onActivateSkill`: whether `skill_name` appeared anywhere in the current
    turn's raw prompt as a `/<name>` reference."""
    is_user_activated: bool | None = None
    """Set only for `onActivateSkill`: whether the current turn's raw prompt *began* with a
    `/<name>` reference to `skill_name`."""
    role: str | None = None
    session_id: str | None = None
    """The firing session's own `Session.id`. `None` only when no live session exists yet."""
    root_session_id: str | None = None
    """The root session's own `Session.root_id`. `None` when no live session exists yet."""
    exit_status: int | None = None
    """The klorb process's own exit status, set only for `onProcessEnd`. Read-only: a handler's
    `HookOutput` cannot change it."""
    workspace_trusted: bool | None = None
    """Whether the workspace is trusted, as of `onSessionStart` firing."""
    workspace_just_bootstrapped: bool | None = None
    """Whether this `onSessionStart` firing is what triggered a first-time workspace trust
    decision. `None` for every hook other than `onSessionStart`."""
    config: dict[str, Any] | None = None
    """The entire resolved `ProcessConfig`, JSON-dumped."""


class HookOutput(BaseModel):
    """What a hook handler returns: whether to proceed, any rewritten tool args, a permission
    verdict, a message to inject into the conversation, and whether that message should
    interrupt an in-flight turn rather than wait for the next natural delivery point.
    """

    success: bool = True
    tool_args: dict[str, Any] | None = None
    permission: Verdict | None = None
    message: str | None = None
    tool_result: str | None = None
    """A rewrite of `onToolResult`'s `HookInput.tool_result`, replacing the tool call's own
    result content in the envelope sent back to the model. Only `onToolResult` acts on this."""
    interrupt: bool = False
    reset_session: bool = False
    """Wipe the firing session's conversation and start it over in place (same `id`/on-disk
    directory), seeded with `message` as its next turn. Only `onAgentTurnEnd` and the event
    hooks act on this. Valid only alongside a non-empty `message`; `HookDispatcher` drops an
    aggregate result that sets this without one, logged at `warning`."""
    log: str | None = None
    """A debugging note, distinct from `message`: never sent to the model, only logged at `info`
    and surfaced verbatim to whichever UI is attached to the firing session."""


class FileSystemUpdate(BaseModel):
    """One filesystem change folded into a debounced `EventInput.fs_updates` batch."""

    event: Literal["created", "deleted", "modified"]
    path: str


class EventInput(HookInput):
    """What an event handler receives."""

    fs_updates: list[FileSystemUpdate] | None = None
    is_agent_active: bool | None = None
    """Whether the root session's agent is mid-turn at the moment this event fires."""
