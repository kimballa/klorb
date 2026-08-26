# © Copyright 2026 Aaron Kimball
"""Inline `@`-triggered fuzzy agent-nickname finder rendered above the prompt input while the
chat room is the active view. See docs/specs/chat-room.md.
"""

from dataclasses import dataclass
from typing import Sequence

from textual.content import Content
from textual.fuzzy import Matcher
from textual.widgets.option_list import Option

from klorb.tui.constants import PROMPT_INPUT_ID
from klorb.tui.widgets.file_finder import FinderOption, FinderPanel

AGENT_FINDER_ID = "agent-finder"
MAX_AGENT_FINDER_MATCHES = 25
"""How many ranked matches the popup keeps, scrollable within its fixed on-screen height."""


@dataclass(frozen=True)
class AgentMatch:
    """One row the chat room's `@`-mention agent finder can show: a live participant's
    `chat_nickname()` and, for a subagent, its task title."""

    nickname: str
    title: str | None


def _agent_match_content(match: AgentMatch, available_width: int) -> Content:
    """Build the styled row label for `match`: the nickname in the normal foreground, plus its
    task title (truncated to fit) in a muted color when present."""
    title_part = ""
    if match.title:
        budget = available_width - len(match.nickname) - 2
        if budget > 3:
            title = match.title
            if len(title) > budget:
                title = title[:budget - 3] + "..."
            title_part = f": {title}"
    return Content.assemble(match.nickname, (title_part, "$foreground-muted"))


class AgentFinderOption(FinderOption):
    """An `OptionList` row carrying the `AgentMatch` it renders."""

    def __init__(self, match: AgentMatch, available_width: int) -> None:
        super().__init__(_agent_match_content(match, available_width), match)


class AgentFinderPanel(FinderPanel):
    """List of matching chat participants, shown directly above the prompt input while the
    cursor sits inside an `@mention` and the chat room is the active view."""

    def _build_options(self, matches: Sequence[object], available_width: int) -> list[Option]:
        return [AgentFinderOption(m, available_width) for m in matches]  # type: ignore[arg-type]

    def _select_match(self) -> None:
        """Dispatch a click or Enter/Tab selection to `PromptInput.select_agent_match`."""
        from klorb.tui.widgets.prompt_input import PromptInput

        self.screen.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).select_agent_match()


def filter_agent_mentions(
    agents: Sequence[AgentMatch], query: str, *, limit: int = MAX_AGENT_FINDER_MATCHES,
) -> list[AgentMatch]:
    """Return up to `limit` agents from `agents` that match `query`. An empty query returns all
    agents (up to `limit`); a non-empty query ranks by `textual.fuzzy.Matcher` score against each
    agent's nickname."""
    if not query:
        return list(agents[:limit])

    matcher = Matcher(query)
    scored = [(matcher.match(agent.nickname), agent) for agent in agents]
    ranked = sorted(scored, key=lambda pair: -pair[0])
    return [agent for score, agent in ranked if score > 0][:limit]
