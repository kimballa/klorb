# © Copyright 2026 Aaron Kimball
"""The JSON wire schema hook/event dispatch feeds to a `bash` subprocess's stdin (or hands to a
`classifier`/`chat` handler): `HookInput`/`EventInput` describe the lifecycle moment or
occurrence that triggered the handler, `HookOutput` describes what the handler decided.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from klorb.permissions.table import Verdict


class HookInput(BaseModel):
    """What a hook handler receives describing the lifecycle moment that triggered it."""

    model_config = ConfigDict(populate_by_name=True)

    hook: str
    name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    workspace_root: str = Field(alias="workspaceRoot")
    event: str | None = None
    message: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    skill_name: str | None = None
    skill_namespace: str | None = None
    is_user_mentioned: bool | None = None
    """Set only for `onActivateSkill`: whether `skill_name` appeared anywhere in the current
    turn's raw prompt as a `/<name>` reference -- see
    `klorb.session.mixins.skills.SessionSkillsMixin.fire_activate_skill_hook`."""
    is_user_activated: bool | None = None
    """Set only for `onActivateSkill`: whether the current turn's raw prompt *began* with a
    `/<name>` reference to `skill_name` -- a strict subset of `is_user_mentioned`."""
    role: str | None = None
    session_id: str | None = None
    """The firing session's own `Session.id` -- root or subagent alike -- so a handler script
    can tell which session in the tree an `onToolUse`/`onToolResult` firing (or any other
    hook) belongs to. `None` only when no live session exists yet (`onProcessStart`/
    `onProcessEnd`)."""
    workspace_trusted: bool | None = Field(default=None, alias="workspaceTrusted")
    """Whether the workspace is trusted, as of `onSessionStart` firing -- always set for that
    hook (`None` for every other hook), once trust is settled for this session's startup."""
    workspace_just_bootstrapped: bool | None = Field(default=None, alias="workspaceJustBootstrapped")
    """Whether this `onSessionStart` firing is what triggered a first-time workspace trust
    decision -- `True` only for a brand-new, never-before-seen workspace; `False` for every
    subsequent `onSessionStart` against an already-registered workspace. `None` for every hook
    other than `onSessionStart`."""


class HookOutput(BaseModel):
    """What a hook handler returns: whether to proceed, any rewritten tool args, a permission
    verdict, a message to inject into the conversation, and whether that message should
    interrupt an in-flight turn rather than wait for the next natural delivery point.
    """

    success: bool = True
    tool_args: dict[str, Any] | None = None
    permission: Verdict | None = None
    message: str | None = None
    interrupt: bool = False
    clear_session: bool = False
    """Discard the firing session (as if the user issued a `/clear`) and start a fresh one
    seeded with `message` as its first turn. Only `onSessionEnd`/`onAgentTurnEnd` act on this --
    see `docs/specs/hooks-and-events.md`. Valid only alongside a non-empty `message`;
    `HookDispatcher` drops an aggregate result that sets this without one, logged at `warning`.
    Implies `interrupt` without reading it: the firing session is being torn down regardless of
    whether a turn happens to be in flight, so the "interrupt now vs. wait" distinction that
    field exists for doesn't apply."""


class FileSystemUpdate(BaseModel):
    """One filesystem change folded into a debounced `EventInput.fs_updates` batch."""

    event: Literal["created", "deleted", "modified"]
    path: str


class EventInput(HookInput):
    """What an event handler receives — the same shape as `HookInput`, plus `fs_updates` for a
    `FileSystemModified` event's debounced batch of changes."""

    fs_updates: list[FileSystemUpdate] | None = None
