# © Copyright 2026 Aaron Kimball
"""Persisting and reloading a session tree's chat room. See docs/specs/session-persistence.md.
"""

import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from klorb.agents.chat import ChatMessage
from klorb.schema_envelope import read_versioned_json, write_versioned_json
from klorb.workspace import Workspace
from klorb.workspace.session_store import session_subdir_path

logger = logging.getLogger(__name__)

CHAT_STATE_FILENAME = "chat.json"

CHAT_STATE_SCHEMA_NAME = "klorb-chat"
CHAT_STATE_SCHEMA_VERSION = "1.0.0"


class ChatState(BaseModel):
    """The persisted shape of a `chat.json`'s data (alongside its `schema` envelope): a
    session tree's full `Channel` state as of the moment it was saved."""

    messages: list[ChatMessage] = Field(default_factory=list)
    hwm: dict[str, int] = Field(default_factory=dict)
    next_seq: int = 0
    mention_wake_count: int = 0


def chat_state_path(workspace: Workspace, subdir: str) -> Path:
    """The `chat.json` path for the session tree saved in `sessions/<subdir>/`."""
    return session_subdir_path(workspace, subdir) / CHAT_STATE_FILENAME


def write_chat_state(
    workspace: Workspace, subdir: str, messages: list[ChatMessage], hwm: dict[str, int],
    next_seq: int, mention_wake_count: int,
) -> None:
    """Save a `Channel`'s full state to `sessions/<subdir>/chat.json`, schema-enveloped.
    Overwrites any previous state for this specific `subdir` outright."""
    state = ChatState(
        messages=messages, hwm=hwm, next_seq=next_seq, mention_wake_count=mention_wake_count)
    write_versioned_json(
        chat_state_path(workspace, subdir), state.model_dump(mode="json"),
        schema_name=CHAT_STATE_SCHEMA_NAME, schema_version=CHAT_STATE_SCHEMA_VERSION)


def read_chat_state(workspace: Workspace, subdir: str) -> ChatState | None:
    """Load the chat room saved in `sessions/<subdir>/chat.json`, or `None` if no file exists
    there, it belongs to a different schema, or its data doesn't validate as `ChatState`."""
    data = read_versioned_json(
        chat_state_path(workspace, subdir), expected_schema_name=CHAT_STATE_SCHEMA_NAME)
    if not data:
        return None
    try:
        return ChatState.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "%s failed to validate as a %s save file; leaving it as-is: %s",
            chat_state_path(workspace, subdir), CHAT_STATE_SCHEMA_NAME, exc)
        return None
