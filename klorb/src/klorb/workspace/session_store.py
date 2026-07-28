# © Copyright 2026 Aaron Kimball
"""Persisting and reloading a trusted workspace's saved sessions -- not just the most recent one,
but up to `MAX_RECENT_SESSIONS` of them, each in its own subdirectory, indexed by recency in
`sessions.json`. See docs/specs/session-persistence.md.

`sessions/` lives in the same per-project directory as the prompt-input history file
(`klorb.workspace.input_history.project_history_dir`), under `$KLORB_DATA_DIR`, not inside the
workspace itself -- the same reasoning docs/adrs/store-last-session-under-klorb-data-dir-not-
workspace.md gives for the single-slot design this module replaces:

```text
$KLORB_DATA_DIR/projects/<token>-<basename>/
├── history                       # unchanged: append-only prompt-recall log
├── workspace.lock                # guards read-modify-write of sessions.json
└── sessions/
    ├── sessions.json             # ordered index of recent sessions
    └── <subdir>/
        ├── session.json          # one saved session's full state
        └── session.lock          # held by the live process that owns this session, if any
```

`sessions/` and each `sessions/<subdir>/` are created lazily, exactly like `project_history_dir`
itself. `klorb.session.mixins.persistence.SessionPersistenceMixin` is the only caller that
claims a session directory (via `session_lock_path` + `klorb.lockfile`) and calls
`write_session_state`/`touch_recent_session`; this module owns the on-disk format and the
`workspace.lock`-guarded index update, not session lifecycle policy (when to claim, when to
release) -- see that mixin.
"""

import logging
import shutil
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from klorb.lockfile import acquire_lockfile_with_backoff, create_lockfile
from klorb.message import Message
from klorb.schema_envelope import read_versioned_json, write_versioned_json
from klorb.session.config import SessionConfig
from klorb.session_statistics import SessionStatistics
from klorb.workspace import Workspace
from klorb.workspace.input_history import project_history_dir

logger = logging.getLogger(__name__)

SESSIONS_DIR_NAME = "sessions"
"""Subdirectory of a per-project directory holding `sessions.json` plus one directory per saved
session."""

SESSIONS_LIST_FILENAME = "sessions.json"
SESSION_STATE_FILENAME = "session.json"
WORKSPACE_LOCK_FILENAME = "workspace.lock"
SESSION_LOCK_FILENAME = "session.lock"

SESSIONS_LIST_SCHEMA_NAME = "klorb-session-list"
SESSIONS_LIST_SCHEMA_VERSION = "1.0.0"

SESSION_STATE_SCHEMA_NAME = "klorb-session"
SESSION_STATE_SCHEMA_VERSION = "1.0.0"

MAX_RECENT_SESSIONS = 30
"""Cap on how many sessions `sessions.json` keeps: whenever `touch_recent_session()` writes a
list longer than this, entries past the cap (least recently touched first) have their
directories deleted and are dropped from the index -- except any entry whose `session.lock` is
currently held by a live process, which is kept regardless, so the list can legitimately exceed
this cap while several sessions are simultaneously open. See `_prune_sessions_index`."""


class RecentSession(BaseModel):
    """One `sessions.json` entry: a saved session's id, which subdirectory of `sessions/` it
    lives in, and its display title.

    `session_id` is the session's `Session.id`, which may be renamed in place by the
    session-naming classifier (`klorb.session_naming.rename_session_id`) after this entry is
    first created -- `touch_recent_session()` re-keys the entry by `session_id` on every call, so
    a rename simply becomes a new value for this field on the existing entry, never a duplicate
    or a move. `subdir` is set once, to the session's *original* `Session.id`, when its directory
    is first created (`SessionPersistenceMixin.claim_session_directory`), and never renamed
    afterward -- kept independent of `session_id` so a session's id can change without moving its
    files. `title` mirrors `Session.name`; `None` only in the narrow window before a title has
    been assigned (shouldn't normally be observed on disk -- a session's directory isn't created
    until after that decision is made, see `SessionPersistenceMixin`)."""

    session_id: str
    subdir: str
    title: str | None = None


class SessionsIndexState(BaseModel):
    """The persisted shape of `sessions.json`: `recent_sessions`, most-recently-touched first."""

    recent_sessions: list[RecentSession] = Field(default_factory=list)


class SessionState(BaseModel):
    """The persisted shape of a `session.json`'s data (alongside its `schema` envelope): the
    `SessionConfig`, `Session.id`, `Session.name`, and full message history of the session that
    was saved, as of the moment it was saved."""

    config: SessionConfig
    messages: list[Message]
    statistics: SessionStatistics | None = None
    """Running statistics accumulated during the session, if saved. Absent (``None``) in files
    written by an older klorb version that predates session statistics tracking."""
    session_id: str | None = None
    """The session's `Session.id`, persisted so it can be restored on reload."""
    root_id: str | None = None
    """The session's `Session.root_id`, persisted so it can be restored on reload. Absent
    (`None`) in files written by an older klorb version that predates it."""
    session_name: str | None = None
    """The human-readable session title (classifier-assigned, or the fallback derived by
    `klorb.session_naming.fallback_session_title`), persisted so the TUI's status bar and the
    "Load session" picker can display it without re-deriving it."""
    cur_chainlink_task_id: int | None = None
    """The saved session's `Session.cur_chainlink_task_id`, if any (see
    docs/specs/chainlink-task-tracking.md). Absent (`None`) in files written by an older klorb
    version that predates chainlink task tracking."""


def sessions_dir(workspace: Workspace) -> Path:
    """`sessions/` for `workspace`: `project_history_dir(workspace) / SESSIONS_DIR_NAME`."""
    return project_history_dir(workspace) / SESSIONS_DIR_NAME


def sessions_list_path(workspace: Workspace) -> Path:
    """The `sessions.json` path for `workspace`."""
    return sessions_dir(workspace) / SESSIONS_LIST_FILENAME


def workspace_lock_path(workspace: Workspace) -> Path:
    """The `workspace.lock` path for `workspace` -- guards read-modify-write access to
    `sessions.json`. Lives directly in the per-project directory (a sibling of `sessions/`), not
    inside it, since it's short-lived contention control for the *index* specifically, not a
    per-session concern."""
    return project_history_dir(workspace) / WORKSPACE_LOCK_FILENAME


def session_subdir_path(workspace: Workspace, subdir: str) -> Path:
    """The directory holding one saved session's `session.json`/`session.lock`."""
    return sessions_dir(workspace) / subdir


def session_state_path(workspace: Workspace, subdir: str) -> Path:
    """The `session.json` path for the session saved in `sessions/<subdir>/`."""
    return session_subdir_path(workspace, subdir) / SESSION_STATE_FILENAME


def session_lock_path(workspace: Workspace, subdir: str) -> Path:
    """The `session.lock` path for the session saved in `sessions/<subdir>/`."""
    return session_subdir_path(workspace, subdir) / SESSION_LOCK_FILENAME


def read_sessions_index(workspace: Workspace) -> SessionsIndexState:
    """Load `workspace`'s `sessions.json`, or an empty index if the file doesn't exist, belongs
    to a different schema, or fails to validate as `SessionsIndexState` (a hand-edited or
    otherwise corrupted file) -- treated as "no recent sessions" rather than raised, the same
    fail-open contract `klorb.workspace.last_session.read_last_session` had for a single save
    file. No locking here: `write_versioned_json`'s atomic temp-file-then-`os.replace()` means a
    read can never observe a torn file, so a reader doesn't need to serialize against a
    concurrent updater's short `workspace.lock`-guarded critical section (see
    `touch_recent_session`)."""
    data = read_versioned_json(
        sessions_list_path(workspace), expected_schema_name=SESSIONS_LIST_SCHEMA_NAME)
    if not data:
        return SessionsIndexState()
    try:
        return SessionsIndexState.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "%s failed to validate as a %s file; treating it as empty: %s",
            sessions_list_path(workspace), SESSIONS_LIST_SCHEMA_NAME, exc)
        return SessionsIndexState()


def _write_sessions_index(workspace: Workspace, index: SessionsIndexState) -> None:
    write_versioned_json(
        sessions_list_path(workspace), index.model_dump(mode="json"),
        schema_name=SESSIONS_LIST_SCHEMA_NAME, schema_version=SESSIONS_LIST_SCHEMA_VERSION)


def _prune_sessions_index(workspace: Workspace, index: SessionsIndexState) -> None:
    """Mutate `index.recent_sessions` in place, dropping (and deleting the directory of) every
    entry beyond `MAX_RECENT_SESSIONS` whose `session.lock` isn't currently held by a live
    process -- see `MAX_RECENT_SESSIONS`'s own docstring. Called only from
    `touch_recent_session()`, already holding `workspace.lock`."""
    kept = index.recent_sessions[:MAX_RECENT_SESSIONS]
    overflow = index.recent_sessions[MAX_RECENT_SESSIONS:]
    for entry in overflow:
        lock = create_lockfile(session_lock_path(workspace, entry.subdir))
        if lock.is_held_by_other():
            logger.debug(
                "Keeping locked session %s (subdir=%s) past MAX_RECENT_SESSIONS=%d.",
                entry.session_id, entry.subdir, MAX_RECENT_SESSIONS)
            kept.append(entry)
            continue
        subdir_path = session_subdir_path(workspace, entry.subdir)
        logger.debug("Pruning session directory %s (past MAX_RECENT_SESSIONS=%d).",
                     subdir_path, MAX_RECENT_SESSIONS)
        shutil.rmtree(subdir_path, ignore_errors=True)
    index.recent_sessions = kept


def touch_recent_session(
    workspace: Workspace, session_id: str, subdir: str, title: str | None,
) -> None:
    """Move (or insert) the `session_id`/`subdir`/`title` entry to the front of `workspace`'s
    `sessions.json`, replacing any existing entry for the same `subdir` -- `subdir`, not
    `session_id`, is an entry's stable identity (see `RecentSession.subdir`'s own docstring): a
    session-naming rename changes `session_id` without changing `subdir`, so re-keying by
    `subdir` is what makes that rename update the existing entry in place rather than leaving a
    stale duplicate behind under the old `session_id`. Then prunes anything that falls past
    `MAX_RECENT_SESSIONS` (see `_prune_sessions_index`).

    Guarded by `workspace.lock` (`acquire_lockfile_with_backoff`) for the duration of this one
    read-modify-write; if the lock can't be acquired after retrying (another process is mid
    read-modify-write of its own and doesn't finish within the backoff window), this call is
    skipped entirely -- logged as a warning, not raised, since a missed recency update is
    recoverable (the next `persist_state()` call tries again) and must never interrupt the turn
    that triggered it.
    """
    lock = acquire_lockfile_with_backoff(workspace_lock_path(workspace))
    if lock is None:
        logger.warning(
            "Could not acquire workspace.lock for %s; skipping recent-session update for %s.",
            workspace.path, session_id)
        return
    try:
        index = read_sessions_index(workspace)
        index.recent_sessions = [
            entry for entry in index.recent_sessions if entry.subdir != subdir]
        index.recent_sessions.insert(
            0, RecentSession(session_id=session_id, subdir=subdir, title=title))
        _prune_sessions_index(workspace, index)
        _write_sessions_index(workspace, index)
    finally:
        lock.release()


def write_session_state(
    workspace: Workspace,
    subdir: str,
    config: SessionConfig,
    messages: list[Message],
    statistics: SessionStatistics | None = None,
    session_id: str | None = None,
    root_id: str | None = None,
    session_name: str | None = None,
    cur_chainlink_task_id: int | None = None,
) -> None:
    """Save `config` and `messages` to `sessions/<subdir>/session.json`, schema-enveloped per
    docs/specs/persisted-json-schema-versioning.md. Overwrites any previous state for this
    specific `subdir` outright -- there is only ever one current state per saved session, the
    same way the single-slot design this replaces overwrote its one `last-session.json`; what's
    different is that a workspace now has many `subdir`s, each independently overwritable.
    """
    state = SessionState(
        config=config, messages=messages, statistics=statistics,
        session_id=session_id, root_id=root_id, session_name=session_name,
        cur_chainlink_task_id=cur_chainlink_task_id)
    write_versioned_json(
        session_state_path(workspace, subdir), state.model_dump(mode="json"),
        schema_name=SESSION_STATE_SCHEMA_NAME, schema_version=SESSION_STATE_SCHEMA_VERSION)


def read_session_state(workspace: Workspace, subdir: str) -> SessionState | None:
    """Load the session saved in `sessions/<subdir>/session.json`, or `None` if no file exists
    there, it belongs to a different schema (`read_versioned_json` already logs a warning and
    discards it), or its data doesn't validate as `SessionState` at all -- a required key
    missing entirely, or a present field with a value of the wrong shape. That last case is a
    `pydantic.ValidationError`, caught here and treated the same as "nothing to restore" rather
    than propagating, so a corrupted save file can't crash a restore attempt."""
    data = read_versioned_json(
        session_state_path(workspace, subdir), expected_schema_name=SESSION_STATE_SCHEMA_NAME)
    if not data:
        return None
    try:
        return SessionState.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "%s failed to validate as a %s save file; leaving it as-is: %s",
            session_state_path(workspace, subdir), SESSION_STATE_SCHEMA_NAME, exc)
        return None
