# © Copyright 2026 Aaron Kimball
"""Command palette provider that lets the user manage the active session — clearing it,
inspecting its run-time statistics, loading a previously saved one, or renaming it."""

from typing import Protocol, cast

from textual.app import ComposeResult
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from klorb.session_statistics import SessionStatistics
from klorb.workspace.session_store import RecentSession

CLEAR_SESSION_LABEL = "Clear session"
SHOW_SESSION_STATS_LABEL = "Show session stats"
LOAD_SESSION_LABEL = "Load session"
RENAME_SESSION_LABEL = "Rename session"
RENAME_SESSION_HEADER_TEXT = "Rename session:"
RENAME_SESSION_INPUT_ID = "rename-session-input"
LOAD_SESSION_HEADER_TEXT = "Load session:"
LOAD_SESSION_OPTION_LIST_ID = "load-session-options"


class SupportsSessionClear(Protocol):
    """Structural interface for an App that can clear its active session."""

    def clear_session(self) -> None:
        """Discard the active session's history and start a fresh one."""
        ...


class SupportsSessionStats(Protocol):
    """Structural interface for an App that can report its session statistics."""

    def get_session_statistics(self) -> SessionStatistics:
        """Return the active session's running statistics."""
        ...

    def show_notice(self, message: str, *, error: bool = False) -> None:
        """Report a one-off status/result in the history scroll."""
        ...


class SupportsSessionLoad(Protocol):
    """Structural interface for an App that can list and load previously saved sessions."""

    def list_recent_sessions(self) -> list[RecentSession]:
        """Return this workspace's saved sessions, most recently touched first."""
        ...

    def load_recent_session(self, entry: RecentSession) -> None:
        """Replace the active session with the one recorded by `entry`, or show a notice
        explaining why that wasn't possible (locked or no longer available)."""
        ...


class SupportsSessionRename(Protocol):
    """Structural interface for an App that can rename the active session's title."""

    def rename_session(self, title: str) -> None:
        """Set the active session's display title to `title` and persist the change."""
        ...

    def get_current_session_title(self) -> str:
        """Return the active session's current title, or an empty string if unnamed."""
        ...


class LoadSessionScreen(ModalScreen[None]):
    """Modal listing `entries`' titles (falling back to a raw `session_id` for an entry with no
    title), stacked vertically in an `OptionList` so the user can move between them with the
    up/down arrow keys and confirm with Enter -- mirrors `klorb.tui.commands.theme_commands.
    ThemeSelectionScreen`'s exact shape. Escape dismisses without making a selection. Listing
    order matches `entries`' own order (`sessions.json`'s recency order -- most recent first)."""

    CSS = """
    LoadSessionScreen {
        align: center middle;
    }

    LoadSessionScreen Vertical {
        width: auto;
        height: auto;
        max-height: 80%;
        border: round $accent;
    }

    #load-session-header {
        padding: 0 1;
        text-style: bold;
    }

    LoadSessionScreen OptionList {
        border: none;
        max-height: 20;
    }
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, entries: list[RecentSession]) -> None:
        super().__init__()
        self._entries = entries

    def compose(self) -> ComposeResult:
        options = (entry.title or entry.session_id for entry in self._entries)
        yield Vertical(
            Static(LOAD_SESSION_HEADER_TEXT, id="load-session-header"),
            OptionList(*options, id=LOAD_SESSION_OPTION_LIST_ID),
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        entry = self._entries[event.option_index]
        cast(SupportsSessionLoad, self.app).load_recent_session(entry)
        self.dismiss()


class RenameSessionScreen(ModalScreen[None]):
    """Modal with a single-line text input for entering a new session title. Submitting
    (Enter) applies the rename; Escape dismisses without changing anything. The input is
    pre-filled with the current title so the user can edit in place."""

    CSS = """
    RenameSessionScreen {
        align: center middle;
    }

    RenameSessionScreen Vertical {
        width: 40;
        height: auto;
        border: round $accent;
    }

    #rename-session-header {
        padding: 0 1;
        text-style: bold;
    }

    RenameSessionScreen Input {
        margin: 0 1 1 1;
    }
    """

    BINDINGS = [("escape", "dismiss", "Cancel")]

    def __init__(self, current_title: str) -> None:
        super().__init__()
        self._current_title = current_title

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(RENAME_SESSION_HEADER_TEXT, id="rename-session-header"),
            Input(
                value=self._current_title,
                placeholder="Session title",
                id=RENAME_SESSION_INPUT_ID,
            ),
        )

    def on_mount(self) -> None:
        input_widget = self.query_one(f"#{RENAME_SESSION_INPUT_ID}", Input)
        input_widget.focus()
        input_widget.cursor_position = len(input_widget.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        title = event.value.strip()
        if title:
            cast(SupportsSessionRename, self.app).rename_session(title)
        self.dismiss()


class SessionCommandProvider(Provider):
    """Offers session-management commands (clearing the active session, showing
    session statistics, loading a previously saved one, or renaming it) via the command palette
    — reachable via ``ctrl+p`` or by typing ``>clear`` / ``>show session stats`` /
    ``>load session`` / ``>rename session`` in the prompt
    (see ``docs/specs/command-palette-from-prompt.md``).
    """

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        score = matcher.match(CLEAR_SESSION_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(CLEAR_SESSION_LABEL), self._clear_session)
        score = matcher.match(SHOW_SESSION_STATS_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(SHOW_SESSION_STATS_LABEL), self._show_session_stats)
        score = matcher.match(LOAD_SESSION_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(LOAD_SESSION_LABEL), self._load_session)
        score = matcher.match(RENAME_SESSION_LABEL)
        if score > 0:
            yield Hit(score, matcher.highlight(RENAME_SESSION_LABEL), self._rename_session)

    async def discover(self) -> Hits:
        yield DiscoveryHit(CLEAR_SESSION_LABEL, self._clear_session)
        yield DiscoveryHit(SHOW_SESSION_STATS_LABEL, self._show_session_stats)
        yield DiscoveryHit(LOAD_SESSION_LABEL, self._load_session)
        yield DiscoveryHit(RENAME_SESSION_LABEL, self._rename_session)

    def _clear_session(self) -> None:
        cast(SupportsSessionClear, self.app).clear_session()

    def _show_session_stats(self) -> None:
        app = cast(SupportsSessionStats, self.app)
        stats = app.get_session_statistics()
        app.show_notice(stats.format_report())

    def _load_session(self) -> None:
        app = cast(SupportsSessionLoad, self.app)
        entries = app.list_recent_sessions()
        self.app.push_screen(LoadSessionScreen(entries))

    def _rename_session(self) -> None:
        current_title = cast(SupportsSessionRename, self.app).get_current_session_title()
        self.app.push_screen(RenameSessionScreen(current_title))
