# © Copyright 2026 Aaron Kimball
"""Owns `projects.json`, klorb's persistent registry of known project roots and whether each is
trusted — the durable half of workspace trust that must survive outside the workspace itself
(so a hostile, downloaded-and-unzipped repository can't ship a pre-trusted `.klorb/` and have it
silently honored). See docs/specs/projects-and-trust.md.

Every read or write of `projects.json` goes through a `TrustManager` instance so there's a
single owner of the file's I/O; the caller (`klorb.cli.main()`, or a test) is responsible for
constructing one `TrustManager` per process and threading it to every place that needs it
(`klorb.process_config.load_process_config`, `klorb.tui.ReplApp`) rather than this module
reaching for a module-level global — see
docs/adrs/00059-thread-trustmanager-explicitly-not-a-global-singleton.md.

`register_project`/`set_trusted` each guard their own load-mutate-save sequence with
`workspaces.lock` (`TrustManager._workspaces_lock_path()`, `klorb.lockfile.
acquire_lockfile_with_backoff`), closing the race two *separate klorb processes* (not just two
`TrustManager` instances in the same process) could otherwise hit writing `projects.json` at once.
"""

import logging
import uuid
from pathlib import Path

from pydantic import BaseModel

from klorb.lockfile import acquire_lockfile_with_backoff
from klorb.paths import KLORB_DATA_DIR
from klorb.permissions.directory_access import find_workspace_root
from klorb.schema_envelope import read_versioned_json, write_versioned_json

from . import Workspace

logger = logging.getLogger(__name__)

PROJECTS_SCHEMA_NAME = "klorb-projects"
PROJECTS_SCHEMA_VERSION = "1.0.0"
PROJECTS_FILENAME = "projects.json"
WORKSPACES_LOCK_FILENAME = "workspaces.lock"

_PROJECTS_KEY = "projects"


class ProjectRecord(BaseModel):
    """One `projects.json` entry: a registered project root and whether it's trusted.

    `path` is stored (and compared) canonicalized (`Path.resolve()`) — see
    `TrustManager.register_project`."""

    id: str
    path: Path
    trusted: bool


def projects_path() -> Path:
    """Where `projects.json` lives: `$KLORB_DATA_DIR/projects.json`. Resolved lazily (a
    function, not a module-level constant) so a `KLORB_DATA_DIR` override applied via
    `monkeypatch`/`.env` after `klorb.paths` import time is still honored by a fresh
    `TrustManager()`, mirroring `klorb.process_config.user_config_path()`."""
    return KLORB_DATA_DIR / PROJECTS_FILENAME


class TrustManager:
    """Owns all reads and writes to `projects.json` and implements the (non-interactive half
    of the) workspace-resolution algorithm from docs/specs/projects-and-trust.md.

    Constructed once per process (or once per test) and passed explicitly to every collaborator
    that needs it — see the module docstring. `path`, if given, overrides where `projects.json`
    is read from/written to (tests use this to avoid touching a real `$KLORB_DATA_DIR`);
    defaults to `projects_path()`.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else projects_path()

    def _workspaces_lock_path(self) -> Path:
        """Where `workspaces.lock` lives for this `TrustManager`: alongside `self._path` (its
        own `projects.json`), not a fixed `$KLORB_DATA_DIR` location -- so a `TrustManager`
        constructed with an overridden `path` (every test in this module) locks next to the
        file it's actually writing, not the developer's real `$KLORB_DATA_DIR`. In production
        `self._path` is always `projects_path()`, so this resolves to
        `$KLORB_DATA_DIR/workspaces.lock` exactly as it would with a fixed location."""
        return self._path.parent / WORKSPACES_LOCK_FILENAME

    def _load(self) -> list[ProjectRecord]:
        raw = read_versioned_json(self._path, expected_schema_name=PROJECTS_SCHEMA_NAME)
        return [ProjectRecord.model_validate(entry) for entry in raw.get(_PROJECTS_KEY, [])]

    def _save(self, records: list[ProjectRecord]) -> None:
        write_versioned_json(
            self._path,
            {_PROJECTS_KEY: [record.model_dump(mode="json") for record in records]},
            schema_name=PROJECTS_SCHEMA_NAME, schema_version=PROJECTS_SCHEMA_VERSION)

    def resolve_workspace(self, cwd: Path) -> Workspace:
        """Identify the current workspace root and what's known about it, per
        docs/specs/projects-and-trust.md's "When you first open a project" resolution order —
        the deterministic part only; this never prompts anyone and never writes anything.

        1. If `cwd` itself (canonicalized) has a `projects.json` entry, that's the workspace:
           returned trusted/untrusted exactly as recorded.
        2. Otherwise, if any ancestor of `cwd` has an entry, that ancestor is the workspace root
           (a subdirectory of a known project is still that project).
        3. Otherwise, fall back to `klorb.permissions.directory_access.find_workspace_root()`'s
           own ancestor search for a `.klorb/` directory (which may exist without ever having
           been registered here — e.g. a downloaded-and-unzipped repository that ships its own
           `.klorb/klorb-config.json` — falling back to `cwd` itself if none is found), returned
           as an unregistered (`id=None`, `is_project=False`), untrusted `Workspace`. Callers
           that can interact with a user (the TUI) are expected to offer to register/trust it;
           callers that can't (a headless one-shot run) leave it exactly this conservative.
        """
        canonical_cwd = cwd.resolve(strict=False)
        records = self._load()
        logger.debug(
            "resolve_workspace: cwd=%s canonical_cwd=%s (%d known project(s) in %s)",
            cwd, canonical_cwd, len(records), self._path)

        for record in records:
            if record.path == canonical_cwd:
                logger.debug(
                    "resolve_workspace: exact match on registered project %s (id=%s, trusted=%s)",
                    record.path, record.id, record.trusted)
                return Workspace(id=record.id, path=record.path, is_project=True, trusted=record.trusted)

        for ancestor in canonical_cwd.parents:
            for record in records:
                if record.path == ancestor:
                    logger.debug(
                        "resolve_workspace: ancestor match on registered project %s (id=%s, "
                        "trusted=%s)", record.path, record.id, record.trusted)
                    return Workspace(
                        id=record.id, path=record.path, is_project=True, trusted=record.trusted)

        workspace_root = find_workspace_root(canonical_cwd)
        logger.debug(
            "resolve_workspace: no registered project found; falling back to unregistered, "
            "untrusted workspace at %s", workspace_root)
        return Workspace(path=workspace_root, is_project=False, trusted=False)

    def register_project(self, path: Path, trusted: bool) -> Workspace:
        """Create a new `projects.json` entry for `path` (canonicalized) with a fresh uuid4 id,
        persist it, and return the resulting `Workspace`. Does not check for an existing entry
        at `path` — callers (the workspace-bootstrap flow) only call this for a workspace
        `resolve_workspace()` has already reported as unregistered (`id is None`).
        """
        canonical_path = path.resolve(strict=False)
        record = ProjectRecord(id=str(uuid.uuid4()), path=canonical_path, trusted=trusted)
        lock = acquire_lockfile_with_backoff(self._workspaces_lock_path())
        if lock is None:
            logger.warning(
                "Could not acquire workspaces.lock; registering project %s unlocked.",
                canonical_path)
        try:
            records = self._load()
            records.append(record)
            self._save(records)
        finally:
            if lock is not None:
                lock.release()
        return Workspace(id=record.id, path=record.path, is_project=True, trusted=trusted)

    def set_trusted(self, project_id: str, trusted: bool) -> None:
        """Update the `trusted` flag of the `projects.json` entry with id `project_id` and
        persist it. Raises `KeyError` if no entry has that id — callers only invoke this for a
        `Workspace.id` they already obtained from this same `TrustManager`, so a missing id
        would mean `projects.json` was modified or replaced out from under this process.
        """
        lock = acquire_lockfile_with_backoff(self._workspaces_lock_path())
        if lock is None:
            logger.warning(
                "Could not acquire workspaces.lock; updating trust for project %s unlocked.",
                project_id)
        try:
            records = self._load()
            for index, record in enumerate(records):
                if record.id == project_id:
                    records[index] = record.model_copy(update={"trusted": trusted})
                    self._save(records)
                    return
            raise KeyError(f"No project record with id {project_id!r} in {self._path}")
        finally:
            if lock is not None:
                lock.release()
