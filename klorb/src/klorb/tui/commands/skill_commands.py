# © Copyright 2026 Aaron Kimball
"""Command palette provider offering "Reload skills" — reindexes the process-wide skill catalog
from a fresh disk scan, reflecting any skill added, removed, or edited since the last scan (or
since process start). See `klorb.tools.skill.catalog` and docs/specs/skills.md.
"""

from typing import Protocol, cast

from textual.command import DiscoveryHit, Hit, Hits, Provider

RELOAD_SKILLS_LABEL = "Reload skills"


class SupportsReloadSkills(Protocol):
    """Structural interface for an App that can reindex the skill catalog."""

    def reload_skills(self) -> None:
        """Rebuild the process-wide skill catalog from a fresh disk scan and report how many
        skills it found."""
        ...


class SkillCommandProvider(Provider):
    """Offers "Reload skills" via the command palette (`ctrl+p` or by typing `>reload skills` in
    the prompt) -- rebuilds the process-wide skill catalog `klorb.tools.skill.catalog` otherwise
    only builds once per process, so a skill added or changed on disk becomes visible without
    restarting klorb.
    """

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        score = matcher.match(RELOAD_SKILLS_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(RELOAD_SKILLS_LABEL), self._reload_skills)

    async def discover(self) -> Hits:
        yield DiscoveryHit(RELOAD_SKILLS_LABEL, self._reload_skills)

    def _reload_skills(self) -> None:
        cast(SupportsReloadSkills, self.app).reload_skills()
