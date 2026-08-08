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
    role: str | None = None


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


class FileSystemUpdate(BaseModel):
    """One filesystem change folded into a debounced `EventInput.fs_updates` batch."""

    event: Literal["created", "deleted", "modified"]
    path: str


class EventInput(HookInput):
    """What an event handler receives — the same shape as `HookInput`, plus `fs_updates` for a
    `FileSystemModified` event's debounced batch of changes."""

    fs_updates: list[FileSystemUpdate] | None = None
