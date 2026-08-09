# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.skill.catalog: build_catalogs()'s frontmatter-alias handling."""

import logging
from pathlib import Path

import pytest

from klorb.permissions.skill_access import SkillRules
from klorb.tools.skill.catalog import build_catalogs
from klorb.tools.skill.common import MAX_SKILL_NAME_DISPLAY_LENGTH


def _write_skill(base: Path, name: str, text: str) -> None:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text)


def test_typed_catalog_resolves_frontmatter_alias(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    _write_skill(
        ws / ".klorb" / "skills", "add-cli-flag",
        "---\nname: cli-flag\ndescription: adds a flag\n---\n")

    catalogs = build_catalogs(
        workspace_root=ws, workspace_trusted=True, claude_skills_compat=False,
        skill_rules=SkillRules())

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
            workspace_root=ws, workspace_trusted=True, claude_skills_compat=False,
            skill_rules=SkillRules())

    winner = catalogs.typed.get(("workspace", "helper"))
    assert winner is not None
    assert winner.name == "task-a"
    loser = catalogs.typed.get(("workspace", "task-b"))
    assert loser is not None
    assert loser.name == "task-b"
    assert any(
        "helper" in record.message and "task-b" in record.message for record in caplog.records)


def test_denied_skill_never_enters_either_catalog(tmp_path: Path) -> None:
    """A skill whose skillRules verdict is already "deny" when the catalog is built is absent
    from both `canonical` and `typed` -- not merely filtered out of `discoverable()` -- since a
    "deny" verdict can never become anything else for that identity within this catalog's
    lifetime."""
    ws = tmp_path / "workspace"
    _write_skill(ws / ".klorb" / "skills", "secret", "---\ndescription: hidden\n---\n")

    catalogs = build_catalogs(
        workspace_root=ws, workspace_trusted=True, claude_skills_compat=False,
        skill_rules=SkillRules(deny=[("workspace", "secret")]))

    assert catalogs.canonical.get(("workspace", "secret")) is None
    assert catalogs.typed.get(("workspace", "secret")) is None


def test_over_long_name_truncated_and_still_resolvable(tmp_path: Path) -> None:
    """A directory basename longer than `MAX_SKILL_NAME_DISPLAY_LENGTH` is truncated to build the
    skill's canonical identity -- the catalog key `ActivateSkill` resolves against and what's
    ever advertised to the model -- rather than rejected outright, so a name once advertised is
    always resolvable. The full untruncated basename remains resolvable too, as a `typed`-only
    alias."""
    ws = tmp_path / "workspace"
    long_name = "a" * (MAX_SKILL_NAME_DISPLAY_LENGTH + 20)
    _write_skill(ws / ".klorb" / "skills", long_name, "---\ndescription: d\n---\n")

    catalogs = build_catalogs(
        workspace_root=ws, workspace_trusted=True, claude_skills_compat=False,
        skill_rules=SkillRules())

    truncated_name = long_name[:MAX_SKILL_NAME_DISPLAY_LENGTH]
    assert catalogs.canonical.get(("workspace", long_name)) is None
    skill = catalogs.canonical.get(("workspace", truncated_name))
    assert skill is not None
    assert skill.name == truncated_name
    # The full untruncated basename still resolves -- only via `typed`, as an alias.
    assert catalogs.typed.get(("workspace", long_name)) is skill
    assert catalogs.typed.get(("workspace", truncated_name)) is skill


def test_over_long_name_truncation_landing_on_a_dash_strips_it(tmp_path: Path) -> None:
    """When the character right at the truncation cutoff is a `-`, the naive `name[:64]` slice
    would leave an invalid trailing dash; `build_catalogs` strips it (via `display_skill_name`) so
    the resulting canonical identity is still a well-formed name."""
    ws = tmp_path / "workspace"
    long_name = "a" * (MAX_SKILL_NAME_DISPLAY_LENGTH - 1) + "-" + "b" * 10
    _write_skill(ws / ".klorb" / "skills", long_name, "---\ndescription: d\n---\n")

    catalogs = build_catalogs(
        workspace_root=ws, workspace_trusted=True, claude_skills_compat=False,
        skill_rules=SkillRules())

    canonical_name = "a" * (MAX_SKILL_NAME_DISPLAY_LENGTH - 1)
    skill = catalogs.canonical.get(("workspace", canonical_name))
    assert skill is not None
    assert skill.name == canonical_name
    assert not skill.name.endswith("-")


def test_alias_collision_with_another_skills_canonical_name_is_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    ws = tmp_path / "workspace"
    _write_skill(ws / ".klorb" / "skills", "helper", "---\ndescription: canonical helper\n---\n")
    _write_skill(
        ws / ".klorb" / "skills", "aliased", "---\nname: helper\ndescription: d\n---\n")

    with caplog.at_level(logging.WARNING):
        catalogs = build_catalogs(
            workspace_root=ws, workspace_trusted=True, claude_skills_compat=False,
            skill_rules=SkillRules())

    winner = catalogs.typed.get(("workspace", "helper"))
    assert winner is not None
    assert winner.name == "helper"
    assert any(
        "helper" in record.message and "aliased" in record.message for record in caplog.records)
