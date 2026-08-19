# © Copyright 2026 Aaron Kimball
"""Owns `projects.json`, klorb's persistent registry of known project roots and whether each is
trusted. See docs/specs/projects-and-trust.md.
"""

import logging
import uuid
from pathlib import Path

from pydantic import BaseModel

from klorb.lockfile import acquire_lockfile_with_backoff
from klorb.paths import get_klorb_data_dir
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
    """One `projects.json` entry: a registered project root and whether it's trusted."""

    id: str
    path: Path
    trusted: bool


def projects_path() -> Path:
    """Where `projects.json` lives: `$KLORB_DATA_DIR/projects.json`. Resolved lazily so a
    `KLORB_DATA_DIR` override applied after import time is still honored."""
    return get_klorb_data_dir() / PROJECTS_FILENAME


class TrustManager:
    """Owns all reads and writes to `projects.json` and implements the workspace-resolution
    algorithm from docs/specs/projects-and-trust.md.

    Constructed once per process and passed explicitly to every collaborator that needs it.
    `path`, if given, overrides where `projects.json` is read from/written to; defaults to
    `projects_path()`.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else projects_path()

    def _workspaces_lock_path(self) -> Path:
        """Where `workspaces.lock` lives for this `TrustManager`: alongside `self._path`,
        not at a fixed `$KLORB_DATA_DIR` location."""
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
        docs/specs/projects-and-trust.md. The deterministic, non-interactive part only: never
        prompts and never writes."""
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
        """Create a new `projects.json` entry for `path` with a fresh uuid4 id, persist it, and
        return the resulting `Workspace`. Does not check for an existing entry at `path`."""
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
        persist it. Raises `KeyError` if no entry has that id."""
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
