# © Copyright 2026 Aaron Kimball
"""Tests for klorb.workspace.trust_manager."""

from pathlib import Path

import pytest

from klorb.schema_envelope import read_versioned_json
from klorb.workspace import TrustManager
from klorb.workspace import Workspace
from klorb.workspace.trust_manager import PROJECTS_SCHEMA_NAME
from klorb.workspace.trust_manager import ProjectRecord


# --- resolve_workspace ---


def test_resolve_workspace_with_no_projects_file_and_no_klorb_dir_falls_back_to_cwd(
    tmp_path: Path,
) -> None:
    manager = TrustManager(path=tmp_path / "projects.json")

    workspace = manager.resolve_workspace(tmp_path)

    assert workspace == Workspace(path=tmp_path, is_project=False, trusted=False)


def test_resolve_workspace_falls_back_to_find_workspace_root_when_unregistered(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / ".klorb").mkdir(parents=True)
    nested = project / "src"
    nested.mkdir()
    manager = TrustManager(path=tmp_path / "projects.json")

    workspace = manager.resolve_workspace(nested)

    assert workspace == Workspace(path=project, is_project=False, trusted=False)


def test_resolve_workspace_matches_an_exact_registered_path(tmp_path: Path) -> None:
    manager = TrustManager(path=tmp_path / "projects.json")
    registered = manager.register_project(tmp_path, trusted=True)

    workspace = manager.resolve_workspace(tmp_path)

    assert workspace == registered
    assert workspace.trusted is True
    assert workspace.is_project is True


def test_resolve_workspace_matches_a_registered_ancestor(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    nested = project / "src" / "sub"
    nested.mkdir(parents=True)
    manager = TrustManager(path=tmp_path / "projects.json")
    registered = manager.register_project(project, trusted=False)

    workspace = manager.resolve_workspace(nested)

    assert workspace == registered
    assert workspace.path == project


def test_resolve_workspace_prefers_exact_match_over_ancestor_match(tmp_path: Path) -> None:
    outer = tmp_path
    inner = tmp_path / "inner"
    inner.mkdir()
    manager = TrustManager(path=tmp_path / "projects.json")
    manager.register_project(outer, trusted=False)
    inner_registered = manager.register_project(inner, trusted=True)

    workspace = manager.resolve_workspace(inner)

    assert workspace == inner_registered


# --- register_project ---


def test_register_project_persists_a_new_record_with_a_fresh_uuid(tmp_path: Path) -> None:
    projects_json = tmp_path / "data" / "projects.json"
    manager = TrustManager(path=projects_json)

    workspace = manager.register_project(tmp_path, trusted=True)

    assert workspace.id is not None
    assert workspace.path == tmp_path
    assert workspace.is_project is True
    assert workspace.trusted is True

    raw = read_versioned_json(projects_json, expected_schema_name=PROJECTS_SCHEMA_NAME)
    assert raw["projects"] == [{"id": workspace.id, "path": str(tmp_path), "trusted": True}]


def test_register_project_canonicalizes_the_path(tmp_path: Path) -> None:
    manager = TrustManager(path=tmp_path / "projects.json")
    uncanonicalized = tmp_path / "sub" / ".." / "sub"
    (tmp_path / "sub").mkdir()

    workspace = manager.register_project(uncanonicalized, trusted=False)

    assert workspace.path == tmp_path / "sub"


def test_register_project_appends_to_existing_records(tmp_path: Path) -> None:
    manager = TrustManager(path=tmp_path / "projects.json")
    first = manager.register_project(tmp_path / "one", trusted=True)
    second = manager.register_project(tmp_path / "two", trusted=False)

    assert first.id != second.id
    raw = read_versioned_json(tmp_path / "projects.json", expected_schema_name=PROJECTS_SCHEMA_NAME)
    assert {entry["id"] for entry in raw["projects"]} == {first.id, second.id}


# --- set_trusted ---


def test_set_trusted_updates_and_persists_the_flag(tmp_path: Path) -> None:
    manager = TrustManager(path=tmp_path / "projects.json")
    workspace = manager.register_project(tmp_path, trusted=False)
    assert workspace.id is not None

    manager.set_trusted(workspace.id, True)

    resolved = manager.resolve_workspace(tmp_path)
    assert resolved.trusted is True


def test_set_trusted_leaves_other_records_untouched(tmp_path: Path) -> None:
    manager = TrustManager(path=tmp_path / "projects.json")
    manager.register_project(tmp_path / "one", trusted=False)
    two = manager.register_project(tmp_path / "two", trusted=False)
    assert two.id is not None

    manager.set_trusted(two.id, True)

    resolved_one = manager.resolve_workspace(tmp_path / "one")
    assert resolved_one.trusted is False


def test_set_trusted_raises_key_error_for_unknown_id(tmp_path: Path) -> None:
    manager = TrustManager(path=tmp_path / "projects.json")
    manager.register_project(tmp_path, trusted=False)

    with pytest.raises(KeyError):
        manager.set_trusted("no-such-id", True)


# --- ProjectRecord round-trip / schema ---


def test_projects_json_round_trips_across_manager_instances(tmp_path: Path) -> None:
    projects_json = tmp_path / "projects.json"
    first_manager = TrustManager(path=projects_json)
    registered = first_manager.register_project(tmp_path, trusted=True)

    second_manager = TrustManager(path=projects_json)
    resolved = second_manager.resolve_workspace(tmp_path)

    assert resolved == registered


def test_project_record_stores_canonicalized_path(tmp_path: Path) -> None:
    record = ProjectRecord(id="x", path=tmp_path, trusted=True)
    assert record.path == tmp_path
