# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.skill.catalog: build_catalogs()'s frontmatter-alias handling."""

import logging
from pathlib import Path

import pytest

from klorb.tools.skill.catalog import build_catalogs


def _write_skill(base: Path, name: str, text: str) -> None:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text)


def test_typed_catalog_resolves_frontmatter_alias(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    _write_skill(
        ws / ".klorb" / "skills", "add-cli-flag",
        "---\nname: cli-flag\ndescription: adds a flag\n---\n")

    catalogs = build_catalogs(workspace_root=ws, workspace_trusted=True, claude_skills_compat=False)

    canonical_skill = catalogs.canonical.get(("workspace", "add-cli-flag"))
    assert canonical_skill is not None
    # The frontmatter name is never a canonical identity, only a `typed`-catalog alias.
    assert catalogs.canonical.get(("workspace", "cli-flag")) is None
    assert catalogs.typed.get(("workspace", "add-cli-flag")) is canonical_skill
    assert catalogs.typed.get(("workspace", "cli-flag")) is canonical_skill


def test_alias_collision_between_two_skills_resolves_deterministically(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    # Two skills whose frontmatter both claim the name "helper" collide as an alias (neither
    # collides with an existing canonical name). The alphabetically-first canonical name wins
    # the alias deterministically; the loser is still resolvable by its own canonical name, just
    # not by "helper", and the collision is logged rather than silently dropped.
    ws = tmp_path / "workspace"
    for dirname in ("task-a", "task-b"):
        _write_skill(
            ws / ".klorb" / "skills", dirname, "---\nname: helper\ndescription: d\n---\n")

    with caplog.at_level(logging.WARNING):
        catalogs = build_catalogs(
            workspace_root=ws, workspace_trusted=True, claude_skills_compat=False)

    winner = catalogs.typed.get(("workspace", "helper"))
    assert winner is not None
    assert winner.name == "task-a"
    loser = catalogs.typed.get(("workspace", "task-b"))
    assert loser is not None
    assert loser.name == "task-b"
    assert any(
        "helper" in record.message and "task-b" in record.message for record in caplog.records)


def test_alias_collision_with_another_skills_canonical_name_is_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    ws = tmp_path / "workspace"
    _write_skill(ws / ".klorb" / "skills", "helper", "---\ndescription: canonical helper\n---\n")
    _write_skill(
        ws / ".klorb" / "skills", "aliased", "---\nname: helper\ndescription: d\n---\n")

    with caplog.at_level(logging.WARNING):
        catalogs = build_catalogs(
            workspace_root=ws, workspace_trusted=True, claude_skills_compat=False)

    winner = catalogs.typed.get(("workspace", "helper"))
    assert winner is not None
    assert winner.name == "helper"
    assert any(
        "helper" in record.message and "aliased" in record.message for record in caplog.records)
