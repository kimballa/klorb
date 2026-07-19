# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.commands.skill_commands."""

from unittest.mock import MagicMock

from klorb.tui.commands.skill_commands import RELOAD_SKILLS_LABEL, SkillCommandProvider


def test_reload_skills_calls_app_reload_skills() -> None:
    mock_screen = MagicMock()
    provider = SkillCommandProvider(mock_screen)

    provider._reload_skills()

    mock_screen.app.reload_skills.assert_called_once_with()


async def test_discover_yields_reload_skills_hit() -> None:
    provider = SkillCommandProvider(MagicMock())

    hits = [hit async for hit in provider.discover()]

    assert any(hit.text == RELOAD_SKILLS_LABEL for hit in hits)


async def test_search_matches_label_query() -> None:
    provider = SkillCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("reload")]

    assert any(RELOAD_SKILLS_LABEL in str(hit.text) for hit in hits)


async def test_search_does_not_match_unrelated_query() -> None:
    provider = SkillCommandProvider(MagicMock())

    hits = [hit async for hit in provider.search("not-a-real-command-xyz")]

    assert hits == []
