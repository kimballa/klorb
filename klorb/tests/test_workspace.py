# © Copyright 2026 Aaron Kimball
"""Tests for klorb.workspace.Workspace."""

from pathlib import Path

from klorb.workspace import Workspace


def test_defaults_are_unregistered_and_untrusted(tmp_path: Path) -> None:
    workspace = Workspace(path=tmp_path)

    assert workspace.id is None
    assert workspace.is_project is False
    assert workspace.trusted is False


def test_fields_round_trip_through_model_copy(tmp_path: Path) -> None:
    workspace = Workspace(id="some-uuid", path=tmp_path, is_project=True, trusted=True)

    copy = workspace.model_copy(update={"trusted": False})

    assert copy.id == "some-uuid"
    assert copy.path == tmp_path
    assert copy.is_project is True
    assert copy.trusted is False
    # The original is untouched -- model_copy() never mutates in place.
    assert workspace.trusted is True


def test_equality_is_by_value() -> None:
    a = Workspace(id="x", path=Path("/some/project"), is_project=True, trusted=True)
    b = Workspace(id="x", path=Path("/some/project"), is_project=True, trusted=True)

    assert a == b
