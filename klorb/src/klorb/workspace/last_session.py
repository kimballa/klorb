# © Copyright 2026 Aaron Kimball
"""Persisting and reloading a previous interactive session's state — its `SessionConfig` and
message history — so a trusted workspace can pick a conversation back up where a previous
klorb process left off. See docs/specs/session-persistence.md.

`last-session.json` lives in the same per-project directory as the prompt-input history file
(`klorb.workspace.input_history.project_history_dir`), under `$KLORB_DATA_DIR`, not inside the
workspace itself — see docs/adrs/store-last-session-under-klorb-data-dir-not-workspace.md for
why.
"""

from pathlib import Path

from pydantic import BaseModel

from klorb.message import Message
from klorb.schema_envelope import read_versioned_json, write_versioned_json
from klorb.session import SessionConfig
from klorb.workspace import Workspace
from klorb.workspace.input_history import project_history_dir

LAST_SESSION_FILENAME = "last-session.json"
"""Name of the saved-session-state file within a per-project directory (see
`klorb.workspace.input_history.project_history_dir`)."""

LAST_SESSION_SCHEMA_NAME = "klorb-session"
LAST_SESSION_SCHEMA_VERSION = "1.0.0"


class LastSessionState(BaseModel):
    """The persisted shape of `last-session.json`'s data (alongside its `schema` envelope):
    the `SessionConfig` and full message history of the session that was saved, as of the
    moment it was saved."""

    config: SessionConfig
    messages: list[Message]


def last_session_path(workspace: Workspace) -> Path:
    """The `last-session.json` path for `workspace`: its per-project directory (shared with
    the input-history store) plus `LAST_SESSION_FILENAME`."""
    return project_history_dir(workspace) / LAST_SESSION_FILENAME


def write_last_session(workspace: Workspace, config: SessionConfig, messages: list[Message]) -> None:
    """Save `config` and `messages` to `workspace`'s `last-session.json`, schema-enveloped per
    docs/specs/persisted-json-schema-versioning.md. Overwrites any previously-saved state for
    this workspace outright — there is only ever one "last" session per workspace."""
    state = LastSessionState(config=config, messages=messages)
    write_versioned_json(
        last_session_path(workspace), state.model_dump(mode="json"),
        schema_name=LAST_SESSION_SCHEMA_NAME, schema_version=LAST_SESSION_SCHEMA_VERSION)


def read_last_session(workspace: Workspace) -> LastSessionState | None:
    """Load `workspace`'s previously-saved session state, or `None` if none exists (no file),
    it belongs to a different schema (`read_versioned_json` already logs a warning and
    discards it in that case), or it's otherwise unparseable as `LastSessionState`."""
    data = read_versioned_json(
        last_session_path(workspace), expected_schema_name=LAST_SESSION_SCHEMA_NAME)
    if not data:
        return None
    return LastSessionState.model_validate(data)
