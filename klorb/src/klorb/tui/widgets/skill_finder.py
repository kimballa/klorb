# © Copyright 2026 Aaron Kimball
"""Inline `/`-triggered fuzzy skill finder rendered above the prompt input. Mirrors
`klorb.tui.widgets.file_finder`'s `@`-mention file finder: when the user types `/` at the
start of the prompt or after whitespace, a popup narrows to matching skills as more of the
query is typed. See docs/specs/skills.md.
"""

from dataclasses import dataclass
from typing import Sequence

from textual.content import Content
from textual.fuzzy import Matcher
from textual.widgets.option_list import Option

from klorb.tui.constants import PROMPT_INPUT_ID
from klorb.tui.widgets.file_finder import FinderOption, FinderPanel

SKILL_FINDER_ID = "skill-finder"
MAX_SKILL_FINDER_MATCHES = 25
"""How many ranked matches the popup keeps, scrollable within its fixed on-screen height."""


@dataclass(frozen=True)
class SkillQuery:
    """Where the currently active `/skill` query starts on the prompt's current line (the
    column of `/` itself) and what's been typed after it up to the cursor."""

    start_column: int
    query: str


def detect_skill_query(line: str, cursor_column: int) -> SkillQuery | None:
    """Find the `/skill` query (if any) `cursor_column` sits inside of on `line`.

    The `/` must be preceded by the start of the line or whitespace (so URLs like
    `https://example.com/path` don't trigger). Scans backward from the cursor for the
    nearest `/` not separated from it by whitespace.
    """
    for i in range(cursor_column - 1, -1, -1):
        ch = line[i]
        if ch.isspace():
            return None
        if ch == "/":
            if i > 0 and not line[i - 1].isspace():
                return None
            return SkillQuery(start_column=i, query=line[i + 1:cursor_column])
    return None


@dataclass(frozen=True)
class SkillMatch:
    """One row the skill fuzzy finder can show: a skill's canonical name, its namespace, and
    its description (truncated for display)."""

    name: str
    namespace: str
    description: str


def _skill_match_content(match: SkillMatch, available_width: int) -> Content:
    """Build the styled row label for `match`: the skill name (normal foreground),
    namespace in a muted color, and description (more severely muted) if present, so
    the two trailing fields read as visually distinct rather than blending into one
    run-on string."""
    name_part = match.name
    ns_part = f" ({match.namespace})"
    desc_part = ""
    if match.description:
        desc_budget = available_width - len(name_part) - len(ns_part) - 2
        if desc_budget > 3:
            desc = match.description
            if len(desc) > desc_budget:
                desc = desc[:desc_budget - 3] + "..."
            desc_part = f": {desc}"
    # `$foreground-*`, not `$text-*`: Textual drops the alpha from a `$text-*` token's
    # `auto NN%` value when it is substituted into an inline `Content` style, so those tokens
    # render at full intensity. The `$foreground-*` tokens expand to an 8-digit hex and keep
    # their alpha.
    return Content.assemble(
        name_part,
        (ns_part, "$foreground-muted"),
        (desc_part, "$foreground-disabled"),
    )


class SkillFinderOption(FinderOption):
    """An `OptionList` row carrying the `SkillMatch` it renders."""

    def __init__(self, match: SkillMatch, available_width: int) -> None:
        super().__init__(_skill_match_content(match, available_width), match)


class SkillFinderPanel(FinderPanel):
    """List of matching skills, shown directly above the prompt input while the cursor sits
    inside a `/skill` query at the start of the line or after whitespace."""

    DEFAULT_CSS = """
    SkillFinderPanel {
        display: none;
        width: 1fr;
        height: auto;
        max-height: 7;
        border: none;
        border-top: solid $accent;
        background: $panel;
        & > .option-list--option {
            padding: 0 1 0 0;
        }
    }
    """

    def _build_options(self, matches: Sequence[object], available_width: int) -> list[Option]:
        return [SkillFinderOption(m, available_width) for m in matches]  # type: ignore[arg-type]

    def _select_match(self) -> None:
        """Dispatch a click or Enter/Tab selection to `PromptInput.select_skill_match`."""
        from klorb.tui.widgets.prompt_input import PromptInput

        self.screen.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).select_skill_match()


def filter_skills(
    skills: Sequence[SkillMatch], query: str, *, limit: int = MAX_SKILL_FINDER_MATCHES,
) -> list[SkillMatch]:
    """Return up to `limit` skills from `skills` that match `query`.

    An empty query returns all skills (up to `limit`); a non-empty query ranks by
    the higher of `textual.fuzzy.Matcher` scores against `name` and `description`.
    """
    if not query:
        return list(skills[:limit])

    matcher = Matcher(query)

    def score_of(skill: SkillMatch) -> float:
        name_score = matcher.match(skill.name)
        desc_score = matcher.match(skill.description) if skill.description else 0.0
        return max(name_score, desc_score)

    scored = [(score_of(skill), skill) for skill in skills]
    ranked = sorted(scored, key=lambda pair: -pair[0])
    return [skill for score, skill in ranked if score > 0][:limit]
