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
    """Why `hook` fired, for a hook whose `HOOK_FILTER_SUBJECT_FIELDS` subject is `"reason"`
    (`onProcessStart`/`onProcessEnd`/`onSessionStart`/`onSessionEnd`/`onRequestPermission`) --
    e.g. `"NewSession"`/`"ResumeSession"` for `onSessionStart`, `"Startup"`/`"Shutdown"` for
    `onProcessStart`/`onProcessEnd`. Distinct from an *Event* (`FileSystemModified`/`Timer`/
    `WorkspaceTrustChanged`), whose own name is carried in `hook`, not here -- an event handler
    never sets `reason`."""
    message: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_result: str | None = None
    """`onToolResult` only: the tool call's own substantive result content -- `response_body` if
    set, else `error_message`, JSON-serialized if not already a plain string. Never includes
    `system_interjections`/`user_interjections`; a hook can't see or override those."""
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
    root_session_id: str | None = None
    """The root session's own `Session.root_id` -- identical to `session_id` for a root
    session's own firing, the ancestor's id for a subagent's. `None` when no live session exists yet."""
    exit_status: int | None = None
    """The klorb process's own exit status, set only for `onProcessEnd`. Read-only: a handler's
    `HookOutput` cannot change it."""
    workspace_trusted: bool | None = None
    """Whether the workspace is trusted, as of `onSessionStart` firing -- always set for that
    hook (`None` for every other hook), once trust is settled for this session's startup."""
    workspace_just_bootstrapped: bool | None = None
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
    tool_result: str | None = None
    """A rewrite of `onToolResult`'s `HookInput.tool_result`, replacing the tool call's own
    result content in the envelope sent back to the model. Only `onToolResult` acts on this."""
    interrupt: bool = False
    reset_session: bool = False
    """Wipe the firing session's conversation and start it over in place (same `id`/on-disk
    directory), seeded with `message` as its next turn -- see `Session.reset_session()` and
    docs/specs/hooks-and-events.md's "Session reset" section. Only `onSessionEnd`/
    `onAgentTurnEnd` act on this. Valid only alongside a non-empty `message`; `HookDispatcher`
    drops an aggregate result that sets this without one, logged at `warning`."""
    log: str | None = None
    """A debugging note, distinct from `message`: never sent to the model, only logged at `info`
    (`HookDispatcher._run_chain`) and surfaced verbatim to whichever UI is attached to the firing
    session (`Session.deliver_notice`) -- the TUI/webview history as a neutral notice, or stdout
    in headless execution. See docs/specs/hooks-and-events.md's "Debugging: `HookOutput.log`"
    section."""


class FileSystemUpdate(BaseModel):
    """One filesystem change folded into a debounced `EventInput.fs_updates` batch."""

    event: Literal["created", "deleted", "modified"]
    path: str


class EventInput(HookInput):
    """What an event handler receives — the same shape as `HookInput`, plus `fs_updates` for a
    `FileSystemModified` event's debounced batch of changes and `is_agent_active` for every
    event."""

    fs_updates: list[FileSystemUpdate] | None = None
    is_agent_active: bool | None = None
    """Whether the root session's agent is mid-turn at the moment this event fires -- set for
    every event."""
