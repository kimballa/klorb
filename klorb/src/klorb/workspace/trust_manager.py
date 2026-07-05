# © Copyright 2026 Aaron Kimball
"""Owns `projects.json`, klorb's persistent registry of known project roots and whether each is
trusted — the durable half of workspace trust that must survive outside the workspace itself
(so a hostile, downloaded-and-unzipped repository can't ship a pre-trusted `.klorb/` and have it
silently honored). See docs/specs/projects-and-trust.md.

Every read or write of `projects.json` goes through a `TrustManager` instance so there's a
single owner of the file's I/O; the caller (`klorb.cli.main()`, or a test) is responsible for
constructing one `TrustManager` per process and threading it to every place that needs it
(`klorb.process_config.load_process_config`, `klorb.tui.repl.ReplApp`) rather than this module
reaching for a module-level global — see docs/adrs/thread-trustmanager-explicitly-not-a-global-singleton.md.
"""

import uuid
from pathlib import Path

from pydantic import BaseModel

from klorb.paths import KLORB_DATA_DIR
from klorb.permissions.directory_access import find_workspace_root
from klorb.schema_envelope import read_versioned_json
from klorb.schema_envelope import write_versioned_json

from . import Workspace

PROJECTS_SCHEMA_NAME = "klorb-projects"
PROJECTS_SCHEMA_VERSION = "1.0.0"
PROJECTS_FILENAME = "projects.json"

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

        for record in records:
            if record.path == canonical_cwd:
                return Workspace(id=record.id, path=record.path, is_project=True, trusted=record.trusted)

        for ancestor in canonical_cwd.parents:
            for record in records:
                if record.path == ancestor:
                    return Workspace(
                        id=record.id, path=record.path, is_project=True, trusted=record.trusted)

        return Workspace(path=find_workspace_root(canonical_cwd), is_project=False, trusted=False)

    def register_project(self, path: Path, trusted: bool) -> Workspace:
        """Create a new `projects.json` entry for `path` (canonicalized) with a fresh uuid4 id,
        persist it, and return the resulting `Workspace`. Does not check for an existing entry
        at `path` — callers (the workspace-bootstrap flow) only call this for a workspace
        `resolve_workspace()` has already reported as unregistered (`id is None`).
        """
        canonical_path = path.resolve(strict=False)
        record = ProjectRecord(id=str(uuid.uuid4()), path=canonical_path, trusted=trusted)
        records = self._load()
        records.append(record)
        self._save(records)
        return Workspace(id=record.id, path=record.path, is_project=True, trusted=trusted)

    def set_trusted(self, project_id: str, trusted: bool) -> None:
        """Update the `trusted` flag of the `projects.json` entry with id `project_id` and
        persist it. Raises `KeyError` if no entry has that id — callers only invoke this for a
        `Workspace.id` they already obtained from this same `TrustManager`, so a missing id
        would mean `projects.json` was modified or replaced out from under this process.
        """
        records = self._load()
        for index, record in enumerate(records):
            if record.id == project_id:
                records[index] = record.model_copy(update={"trusted": trusted})
                self._save(records)
                return
        raise KeyError(f"No project record with id {project_id!r} in {self._path}")
