# © Copyright 2026 Aaron Kimball
"""A Textual-based interactive REPL: a scrolling prompt/response history with an input
box pinned to the bottom of the screen.
"""

import inspect
import logging
import threading
from collections.abc import Iterable
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import events
from textual import work
from textual.app import App
from textual.app import ComposeResult
from textual.app import SystemCommand
from textual.binding import Binding
from textual.command import DiscoveryHit
from textual.command import Hit
from textual.containers import Horizontal
from textual.containers import Vertical
from textual.containers import VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.screen import ModalScreen
from textual.screen import Screen
from textual.types import IgnoreReturnCallbackType
from textual.widgets import Button
from textual.widgets import Footer
from textual.widgets import Header
from textual.widgets import Markdown
from textual.widgets import Static
from textual.widgets import TextArea

from klorb.api_provider import ResponseAborted
from klorb.logging_config import configure_logging
from klorb.logging_config import session_log_path
from klorb.permissions.directory_access import DirRules
from klorb.permissions.grant import compute_grant_paths
from klorb.process_config import ProcessConfig
from klorb.process_config import load_process_config
from klorb.process_config import persist_session_default
from klorb.process_config import persist_theme
from klorb.process_config import project_config_path
from klorb.process_config import user_config_path
from klorb.session import PermissionAskContext
from klorb.session import PermissionDecision
from klorb.session import Session
from klorb.session import ThinkingEffort
from klorb.session import ToolCallEvent
from klorb.session import TurnEventHandlers
from klorb.tools.registry import ToolRegistry
from klorb.tools.tool import default_tool_call_detail
from klorb.tools.tool import default_tool_call_summary
from klorb.tui.confirm_screen import ConfirmScreen
from klorb.tui.init_commands import INIT_CONFIG_LABEL
from klorb.tui.init_commands import InitCommandProvider
from klorb.tui.model_commands import ModelCommandProvider
from klorb.tui.palette import PALETTE_PREFIX
from klorb.tui.palette import PROMPT_PALETTE_ID
from klorb.tui.palette import PromptPalette
from klorb.tui.palette import gather_palette_hits
from klorb.tui.permission_ask_screen import PermissionAskScreen
from klorb.tui.session_commands import SessionCommandProvider
from klorb.tui.shell import ShellCommandCancelled
from klorb.tui.shell import ShellCommandTimedOut
from klorb.tui.shell import UserShellCommand
from klorb.tui.theme_commands import ThemeCommandProvider
from klorb.tui.thinking_commands import ThinkingCommandProvider
from klorb.tui.trust_commands import TRUST_WORKSPACE_LABEL
from klorb.tui.trust_commands import TrustWorkspaceCommandProvider
from klorb.workspace import TrustManager
from klorb.workspace import Workspace
from klorb.workspace.workspace_init import write_initial_project_config
from klorb.workspace.workspace_init import write_session_defaults_to_project_config

logger = logging.getLogger(__name__)


def _concat_dir_rules(base: DirRules, addition: DirRules) -> DirRules:
    """Concatenate `addition`'s deny/ask/allow entries onto `base`'s own, per category —
    never replacing what's already there. Used by `ReplApp._apply_workspace_config` to fold a
    freshly-(re)loaded `Workspace`'s config-file grants into a live `SessionConfig` without
    discarding any in-session-only grant ("Allow (this session)") already present; duplicate
    entries across the two are harmless (see docs/specs/permissions.md — evaluation is by
    category membership, not list position, so redundancy costs nothing but a few extra
    entries in the list)."""
    return DirRules(
        deny=list(base.deny) + list(addition.deny),
        ask=list(base.ask) + list(addition.ask),
        allow=list(base.allow) + list(addition.allow),
    )


HISTORY_ID = "history"
PROMPT_INPUT_ID = "prompt-input"
PALETTE_HINT_ID = "palette-hint"
PALETTE_HINT_TEXT = f"{PALETTE_PREFIX} palette"
STATUS_BAR_ID = "status-bar"
THINKING_LABEL = "<Thinking>"
TOOL_USE_LABEL = "<Tool use>"
CONFIG_MISSING_MESSAGE = (
    f"Klorb configuration file not found. Run `{PALETTE_PREFIX}{INIT_CONFIG_LABEL}` to set up.")
MASCOT_ART = """\
      o
     /
    ▄▄▄
   █████
  ███████
 █░███x███
███████████
▟█▙     ▟█▙"""
MASCOT_GREETING = "Roar! Let's go code a Thing!"

_SI_SUFFIXES = ("", "k", "M", "B")


def _round_to_2_sig_figs(value: float) -> float:
    """Round `value` (>= 1) to 2 significant figures, e.g. `1.43` -> `1.4`, `423` -> `420`."""
    if value < 10:
        return round(value, 1)
    if value < 100:
        return float(round(value))
    return float(round(value / 10) * 10)


def format_token_count(count: int) -> str:
    """Render `count` using SI-style suffixes at 2 significant figures once >= 1000, e.g.
    `1400` -> `"1.4k"`, `23000` -> `"23k"`, `423000` -> `"420k"`. Counts under 1000 are
    shown as a literal integer.
    """
    if count < 1000:
        return str(count)

    magnitude = 0
    value = float(count)
    while value >= 1000 and magnitude < len(_SI_SUFFIXES) - 1:
        value /= 1000
        magnitude += 1

    value = _round_to_2_sig_figs(value)
    if value >= 1000 and magnitude < len(_SI_SUFFIXES) - 1:
        value /= 1000
        magnitude += 1

    suffix = _SI_SUFFIXES[magnitude]
    if value == int(value):
        return f"{int(value)}{suffix}"
    return f"{value}{suffix}"


_WORKSPACE_PATH_DISPLAY_MAX_CHARS = 40


def _format_workspace_path(
    path: Path, max_chars: int = _WORKSPACE_PATH_DISPLAY_MAX_CHARS,
) -> str:
    """Render `path` for the header: the full path if it's at most `max_chars` characters long,
    else just its last two components prefixed with `"..."` (e.g. `".../last/two_parts"`).
    """
    full = str(path)
    if len(full) <= max_chars or len(path.parts) < 2:
        return full
    return ".../" + "/".join(path.parts[-2:])


def _italicized(text: str) -> str:
    """Wrap `text` in Rich console markup for italics, escaping any literal `[`/`]` in
    `text` first so it can't be misread as markup by the `Static` widget it's rendered in.
    """
    return f"[italic]{escape(text)}[/italic]"


def _pinned_to_bottom(history: VerticalScroll) -> bool:
    """Whether `history`'s viewport is currently showing its bottom edge, i.e. whether the
    user hasn't scrolled away from the latest content.

    Only `ReplApp._on_history_scroll_changed` should call this: it's accurate exactly when
    `history`'s `scroll_y` has just changed (mounting a widget doesn't itself update
    `max_scroll_y` until the next layout refresh, so calling this at an arbitrary other time
    can read a stale comparison). Everywhere else that needs to know whether to follow new
    content to the bottom reads the cached `ReplApp._history_pinned_to_bottom` instead.
    """
    return history.is_vertical_scroll_end


class PromptInput(TextArea):
    """A multi-line prompt box that soft-wraps long input and grows with it.

    Enter submits the current text, mirroring the single-line `Input` widget this replaces.
    Ctrl+Enter inserts a literal newline instead. Shift+Enter and Alt+Enter aren't usable for
    this: Shift+Enter is indistinguishable from plain Enter on terminals without kitty
    keyboard protocol support (confirmed by testing: both report as plain `"enter"`), and
    Alt+Enter is claimed by Windows/many terminal emulators for toggling fullscreen before it
    ever reaches the app. Ctrl+Enter itself commonly arrives as `"ctrl+j"` rather than
    `"ctrl+enter"`, since terminals typically send the same linefeed control byte for both
    Ctrl+Enter and Ctrl+J; both spellings are handled here.

    Up-arrow at the start of the text and down-arrow at the end of the text walk a per-session
    history of previously-submitted prompts (see `_recall_history`), loading the recalled entry
    into the box for editing and resending; once that walk has started, further up/down presses
    keep walking regardless of where the cursor lands. Walking up from an untouched draft stashes
    that draft's text so walking back down past the most recent entry restores it rather than
    clearing the box. Any text-mutating action (typing, deleting, pasting) detaches the box from
    its current position in that history so the now-edited text is treated as a fresh draft
    rather than a rooted recall; pure cursor movement (arrow keys, home/end, selection) does not
    detach. Clearing the session (see `ReplApp.clear_session()`) resets the history, the recall
    position, and the stashed draft.

    Whenever the text starts with `>` (see `klorb.tui.palette`), up/down/enter instead drive the
    inline `PromptPalette` popup mounted just above this widget rather than history recall or
    submission: see `_on_key`'s palette branch and `_refresh_palette`.
    """

    NEWLINE_KEYS = ("ctrl+j", "ctrl+enter")

    # Keys `TextArea` treats as pure cursor/selection movement (no text mutation), so
    # `_on_key` leaves the recall position untouched for them and lets `TextArea`'s own
    # bindings handle the navigation. Everything else that reaches `_on_key` (printable
    # characters, the newline keys, and the deletion/editing bindings below) mutates the
    # text and detaches the box from history; the deletion/editing bindings are enumerated
    # explicitly because `TextArea` dispatches them via `action_*` methods rather than
    # `_on_key`, so `_on_key` only needs to recognize them to set the detach flag before
    # the binding runs.
    _NAVIGATION_KEYS = frozenset({
        "up", "down", "left", "right",
        "ctrl+left", "ctrl+right",
        "home", "end", "ctrl+a", "ctrl+e",
        "pageup", "pagedown",
        "shift+up", "shift+down", "shift+left", "shift+right",
        "ctrl+shift+left", "ctrl+shift+right", "shift+home", "shift+end",
        "tab",
    })

    # Binding-driven text mutations (see `_NAVIGATION_KEYS`): each deletes, cuts, pastes, or
    # undoes/redoes content. `_on_key` checks membership to detach from history before
    # `TextArea`'s binding dispatches the matching `action_*`.
    _MUTATION_BINDING_KEYS = frozenset({
        "backspace", "delete", "ctrl+d",
        "ctrl+w", "ctrl+backspace", "alt+delete",
        "ctrl+u", "super+backspace", "ctrl+k", "ctrl+shift+k",
        "ctrl+x", "ctrl+v",
        "ctrl+z", "super+z", "ctrl+y", "super+y",
    })

    class Submitted(Message):
        """Posted when the user presses Enter to submit the current text."""

        def __init__(self, prompt_input: "PromptInput", value: str) -> None:
            self.prompt_input = prompt_input
            self.value = value
            super().__init__()

        @property
        def control(self) -> "PromptInput":
            return self.prompt_input

    def on_mount(self) -> None:
        """Initialize the per-instance input-history state.

        Done here rather than in `__init__` (which would need to redeclare `TextArea`'s many
        keyword parameters just to forward them) so the widget keeps `TextArea`'s own
        constructor signature untouched. `TextArea` defines `_on_mount`, not `on_mount`, so
        this handler is purely additive and doesn't shadow one of its own.
        """
        self._history: list[str] = []
        self._history_index: int | None = None
        self._draft: str = ""
        self._palette_dismissed: bool = False
        self._suppress_palette_during_recall: bool = False
        self._last_key: str | None = None

    def clear_input_history(self) -> None:
        """Drop the recorded prompt history and reset the recall position.

        Called by `ReplApp.clear_session()` so a fresh `Session` starts with an empty input
        history to match its empty conversation history.
        """
        self._history.clear()
        self._history_index = None
        self._draft = ""
        self._palette_dismissed = False
        self._suppress_palette_during_recall = False
        self._palette_widget().hide()

    @property
    def _palette_mode(self) -> bool:
        """Whether up/down/enter/escape should drive the `PromptPalette` popup rather than
        history recall or submission: the text starts with `>` and hasn't been dismissed out
        of palette mode for this draft (see `_dismiss_palette`), and the text didn't just get
        there via history recall (`_suppress_palette_during_recall`) — a recalled entry that
        happens to start with `>` (recording an earlier palette selection, see
        `_execute_palette_hit`) is browsed as plain recalled text, not treated as a fresh
        palette query, until the user actually edits it (`_detach_from_history` clears the
        suppression).
        """
        return (
            self.text.startswith(PALETTE_PREFIX)
            and not self._palette_dismissed
            and not self._suppress_palette_during_recall
        )

    def _palette_widget(self) -> PromptPalette:
        """The sibling `PromptPalette` popup mounted just above this widget."""
        return self.screen.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)

    async def _on_key(self, event: events.Key) -> None:
        """Submit on Enter; insert a newline on Ctrl+Enter; walk the input history on
        up/down at the text boundaries; detach from history on any text mutation; defer
        everything else (typing, navigation, selection) to `TextArea`'s own handling; and,
        while `_palette_mode` is active, let up/down/enter/escape drive the `PromptPalette`
        popup instead of any of the above (see `_refresh_palette` for how a keystroke enters
        or leaves palette mode).

        `self._last_key` records the key currently being processed for `on_text_area_changed`
        to read: a mutation-binding key like backspace or delete (see
        `_MUTATION_BINDING_KEYS`) is applied via a `TextArea` action (e.g.
        `action_delete_left`) that Textual dispatches once this whole event — bubbling past
        `_on_key` up to the `App`'s own binding check — has finished, which in practice lands
        even later than a `call_after_refresh`/`call_later` callback scheduled from here would
        run. So `_on_key` can't reliably read the *post*-edit `self.text` for those keys no
        matter how it defers; only `TextArea.Changed` (posted once the edit has actually
        landed, regardless of what caused it) can, which is why `_refresh_palette` is now
        driven from `on_text_area_changed` instead of being called directly from here.
        """
        key = event.key
        self._last_key = key
        if self._palette_mode:
            if key == "escape":
                event.stop()
                event.prevent_default()
                self._dismiss_palette()
                return
            if key == "up":
                event.stop()
                event.prevent_default()
                self._palette_widget().move_highlight(-1)
                return
            if key == "down":
                event.stop()
                event.prevent_default()
                self._palette_widget().move_highlight(1)
                return
            if key == "enter":
                event.stop()
                event.prevent_default()
                hit = self._palette_widget().current_hit
                if hit is None:
                    self._record_and_submit()
                else:
                    self._execute_palette_hit(hit)
                return
        if key == "enter":
            event.stop()
            event.prevent_default()
            self._record_and_submit()
            return
        if key in self.NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self._detach_from_history()
            self.replace("\n", *self.selection)
            return
        if key == "up":
            # Once already mid-recall, further up-presses keep walking regardless of where
            # the previous recall left the cursor (recall lands it at the entry's end, not
            # its start); the boundary check only gates *starting* a fresh walk from a draft.
            at_boundary = self._history_index is not None or self.cursor_at_start_of_text
            if at_boundary and self._recall_history(-1):
                event.stop()
                event.prevent_default()
                return
            await super()._on_key(event)
            return
        if key == "down":
            at_boundary = self._history_index is not None or self.cursor_at_end_of_text
            if at_boundary and self._recall_history(1):
                event.stop()
                event.prevent_default()
                return
            await super()._on_key(event)
            return
        if key not in self._NAVIGATION_KEYS:
            # Any key that isn't pure navigation either inserts/deletes text directly (a
            # printable character or a `_MUTATION_BINDING_KEYS` entry) or is unrecognized;
            # the former detaches the box from history so the edit reads as a fresh draft.
            if key in self._MUTATION_BINDING_KEYS or event.is_printable:
                self._detach_from_history()
        await super()._on_key(event)

    async def _on_paste(self, event: events.Paste) -> None:
        """Detach from history before a paste inserts text, since pasting mutates the box."""
        self._last_key = None
        self._detach_from_history()
        await super()._on_paste(event)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Refresh palette state whenever the text actually changes, regardless of what
        changed it — typing, a `_MUTATION_BINDING_KEYS` action (backspace, delete, cut,
        undo/redo, ...), paste, or `_recall_history`'s own `self.text = ...` assignment.
        `TextArea` posts this exactly when a mutation actually lands, which sidesteps the
        ordering problem `_on_key` has for anything applied via a bound action (see its
        docstring): rather than guessing when that action has run, just react to the signal
        that says it just did. A recalled entry is the one case that must *not* trigger a
        palette refresh despite changing the text (see `_suppress_palette_during_recall`), so
        it's checked first and skipped entirely.
        """
        if self._suppress_palette_during_recall:
            return
        self.call_later(self._refresh_palette, self._last_key)

    async def _refresh_palette(self, key: str | None) -> None:
        """Recompute palette mode from the current text and update the popup to match.

        Text that no longer starts with `>` (typically an empty box, e.g. right after a
        submission) always resets `_palette_dismissed` and hides the popup, so a fresh `>`
        later starts palette mode again. Otherwise, unless already dismissed for this draft,
        this queries every registered command provider (`gather_palette_hits`) for the text
        after the leading `>` and shows the popup if anything matched. If nothing matched and
        `key` is one of the whitespace keys that "give up" on a palette search (space, tab, or
        a literal newline via `NEWLINE_KEYS`), the draft is marked dismissed so the rest of it
        types as an ordinary prompt rather than re-showing the popup on every further
        keystroke.
        """
        if not self.text.startswith(PALETTE_PREFIX):
            self._palette_dismissed = False
            self._palette_widget().hide()
            return
        if self._palette_dismissed:
            return
        query = self.text[len(PALETTE_PREFIX):]
        hits = await gather_palette_hits(self.app, query)
        palette = self._palette_widget()
        if hits:
            palette.show_hits(hits)
            return
        palette.hide()
        if key == "space" or key == "tab" or key in self.NEWLINE_KEYS:
            self._palette_dismissed = True

    def _dismiss_palette(self) -> None:
        """Leave palette mode for the current draft without changing its text, so the user can
        keep typing it as an ordinary prompt (Escape's behavior, per
        `docs/specs/command-palette-from-prompt.md`).
        """
        self._palette_dismissed = True
        self._palette_widget().hide()

    def _execute_palette_hit(self, hit: Hit | DiscoveryHit) -> None:
        """Clear the box and hide the popup, then run the currently-highlighted palette
        command once this key event has finished being handled (`call_later`, mirroring
        Textual's own `CommandPalette._select_or_command`, since a command that itself
        pushes a modal shouldn't do so mid-keystroke). `_run_palette_command` records the
        selection into the input history only after `hit.command` has actually run — see its
        docstring for why that order matters.
        """
        canonical_text = hit.text
        assert canonical_text is not None
        self.text = ""
        self._palette_dismissed = False
        self._palette_widget().hide()
        self.app.call_later(self._run_palette_command, hit.command, canonical_text)

    async def _run_palette_command(
        self, command: IgnoreReturnCallbackType, canonical_text: str,
    ) -> None:
        """Run `command`, awaiting it first if it's async, then record `>canonical_text`
        into the input history.

        Recording only happens *after* `command` has run — not before, and not
        unconditionally — because a command can itself mutate the input history: selecting
        `>Clear session` must still leave `>Clear session` recallable afterward (per
        `docs/specs/command-palette-from-prompt.md`'s worked example), but `clear_session()`
        resets the history via `PromptInput.clear_input_history()` as part of starting a
        fresh session. Appending here, after that reset has already happened, is what makes
        the entry survive as the new history's first (and, until something else is
        submitted, only) entry.
        """
        result = command()
        if inspect.isawaitable(result):
            await result
        self._history.append(f"{PALETTE_PREFIX}{canonical_text}")
        self._history_index = None

    def _detach_from_history(self) -> None:
        """Mark the current text as no longer rooted at a position in the input history, so a
        subsequent up/down at the boundaries resumes recall from the most recent entry rather
        than continuing from a now-stale index. Also lifts a recalled entry's suppression of
        palette mode (see `_palette_mode`), since editing it turns it into a fresh draft that
        a leading `>` should once again be read as a live palette query.
        """
        self._history_index = None
        self._suppress_palette_during_recall = False

    def _record_and_submit(self) -> None:
        """Record the current (non-empty) text into the input history and post `Submitted`.

        Mirrors `ReplApp.on_prompt_input_submitted`'s own empty-prompt guard so an empty or
        whitespace-only submit isn't recorded (and, by not clearing the box here, is left in
        place for the user to keep typing into). The recall position is reset to a fresh draft
        so the next up-arrow walks back from the just-appended entry.
        """
        value = self.text
        if not value.strip():
            self.post_message(self.Submitted(self, value))
            return
        self._history.append(value)
        self._history_index = None
        self.post_message(self.Submitted(self, value))

    def _recall_history(self, direction: int) -> bool:
        """Move the recall position by `direction` (-1 for up/older, +1 for down/newer) and load
        the entry there into the box, returning whether a recall actually happened.

        With no recorded history there's nothing to recall. Otherwise up-arrow from a fresh
        draft (`_history_index is None`) stashes the draft's current text in `_draft` and jumps
        to the most recent entry, then to older ones; down-arrow from the most recent entry
        restores that stashed draft rather than clearing to empty. Recalling loads the entry's
        text verbatim and lands the cursor at its end so the user can append to it, matching the
        common readline behavior of editing a recalled line from its tail. Loading a recalled
        entry also sets `_suppress_palette_during_recall` so a recalled entry starting with `>`
        (e.g. a previously-executed palette selection) is browsed as plain text rather than
        resurfacing the `PromptPalette` popup; restoring the stashed draft clears it again,
        since that's the user's own in-progress text rather than a recalled entry.
        """
        if not self._history:
            return False
        if direction < 0:
            if self._history_index is None:
                self._draft = self.text
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            else:
                return False
        else:
            if self._history_index is None:
                return False
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
            else:
                self._history_index = None
                self._suppress_palette_during_recall = False
                self.text = self._draft
                self.move_cursor(self._last_location())
                return True
        entry = self._history[self._history_index]
        self._suppress_palette_during_recall = True
        self.text = entry
        self.move_cursor(self._last_location())
        return True

    def _last_location(self) -> tuple[int, int]:
        """Return the document location just past the end of the current text."""
        last_row = self.document.line_count - 1
        return (last_row, len(self.document[last_row]))


class ToolCallLimitScreen(ModalScreen[bool]):
    """Modal asking whether to double a tool-call safety limit and keep going, shown when
    `Session` reports one has been reached (see `ReplApp._on_tool_call_limit_reached`).
    "Yes" or Enter (via the focused "Yes" button) confirms; "No" or Escape declines. Left/Right
    arrow keys move focus between the two buttons, same as Tab/Shift+Tab.
    """

    CSS = """
    ToolCallLimitScreen {
        align: center middle;
    }

    ToolCallLimitScreen Vertical {
        width: auto;
        max-width: 60;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }

    #tool-call-limit-message {
        margin: 0 0 1 0;
    }

    #tool-call-limit-buttons {
        align: center middle;
        height: auto;
    }

    #tool-call-limit-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "decline", "No"),
        Binding("left", "app.focus_previous", "Focus Previous", show=False),
        Binding("right", "app.focus_next", "Focus Next", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._message, id="tool-call-limit-message"),
            Horizontal(
                Button("Yes", id="tool-call-limit-yes", variant="primary"),
                Button("No", id="tool-call-limit-no"),
                id="tool-call-limit-buttons",
            ),
        )

    def on_mount(self) -> None:
        self.query_one("#tool-call-limit-yes", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "tool-call-limit-yes")

    def action_decline(self) -> None:
        self.dismiss(False)


class ToolCallStatic(Static):
    """One finished tool call in the history, shown as either its one-line summary or its
    fuller detail view — toggled globally, across every `ToolCallStatic` at once, by Ctrl+O
    (see `ReplApp.action_toggle_tool_call_detail`).
    """

    def __init__(self, summary_text: str, detail_text: str) -> None:
        super().__init__(escape(summary_text), classes="tool-call")
        self._summary_text = summary_text
        self._detail_text = detail_text

    def set_detail_shown(self, show_detail: bool) -> None:
        """Update the rendered content to the detail view if `show_detail`, else the summary."""
        self.update(escape(self._detail_text if show_detail else self._summary_text))


class PaletteHint(Static):
    """Renders `PALETTE_HINT_TEXT` styled like one of `Footer`'s own key-binding chips (e.g.
    `^q Quit`): the leading `>` in `$footer-key-foreground`/`-background` (bold, like a key),
    ` palette` in `$footer-description-foreground`/`-background` — rather than plain
    single-toned text — so it reads as one more binding in the status row instead of an
    unrelated label. Uses its own component classes (`palette-hint--key`/`-description`)
    rather than `Footer`'s private `footer-key--key`/`-description` ones: Textual resolves a
    component class through CSS scoped to the declaring widget type, so borrowing `Footer`'s
    directly wouldn't resolve on a different widget; declaring our own against the same
    theme-level `$footer-*` variables gets the identical look without that coupling. The
    widget's own base `background` is set to `$footer-background` — the same variable
    `Footer` itself uses — rather than left at its default: `$footer-key-background`/
    `$footer-item-background`/`$footer-description-background` all resolve to `transparent`
    in the built-in themes (`Footer`'s own blue comes entirely from its own top-level
    `background: $footer-background`, not from those component-class colors), so without
    this base the hint would composite over the app's own background instead.
    """

    COMPONENT_CLASSES = {"palette-hint--key", "palette-hint--description"}

    DEFAULT_CSS = """
    PaletteHint {
        width: auto;
        height: 1;
        background: $footer-background;
    }
    PaletteHint .palette-hint--key {
        color: $footer-key-foreground;
        background: $footer-key-background;
        text-style: bold;
        padding: 0 1;
    }
    PaletteHint .palette-hint--description {
        color: $footer-description-foreground;
        background: $footer-description-background;
        padding: 0 1 0 0;
    }
    """

    def on_mount(self) -> None:
        """Start hidden until `show_hint()` is first called."""
        self._shown = False

    def render(self) -> Text:
        """Render `PALETTE_PREFIX` and `"palette"` in the two component styles above (or
        nothing, while hidden), padded the same way `FooterKey.render()` pads its own
        key/description. Recomputing the styles here — rather than baking a `Text` once via
        `self.update(...)` in `show_hint()` — is what `FooterKey.render()` itself does too,
        and is why it works: Textual re-invokes `render()` whenever the active theme changes,
        so reading `get_component_rich_style()`/`get_component_styles()` fresh on every call
        (rather than caching their result in a stored renderable) is what keeps this hint's
        colors following a theme switch instead of freezing at whatever they were the moment
        `show_hint()` last ran.
        """
        if not self._shown:
            return Text("")
        key_style = self.get_component_rich_style("palette-hint--key")
        key_padding = self.get_component_styles("palette-hint--key").padding
        description_style = self.get_component_rich_style("palette-hint--description")
        description_padding = self.get_component_styles("palette-hint--description").padding
        return Text.assemble(
            (" " * key_padding.left + PALETTE_PREFIX + " " * key_padding.right, key_style),
            (" " * description_padding.left + "palette" + " " * description_padding.right,
             description_style),
        )

    def show_hint(self) -> None:
        """Show the hint, redrawing via `render()` above."""
        self._shown = True
        self.refresh()

    def hide_hint(self) -> None:
        """Hide the hint, redrawing via `render()` above."""
        self._shown = False
        self.refresh()


class ReplApp(App[None]):
    """Interactive REPL: a scrolling history of prompts/responses, with a bottom input box."""

    CSS = """
    #history {
        height: 1fr;
    }

    #prompt-input, #prompt-input:focus {
        border: none;
        border-top: solid $accent;
        height: auto;
        padding: 0 1;
    }

    #status-row {
        dock: bottom;
        height: 1;
    }

    #status-row Footer {
        dock: none;
        width: 1fr;
        height: 1;
    }

    #status-bar {
        width: auto;
        color: $footer-description-foreground;
        background: $footer-background;
        padding: 0 1;
    }

    .prompt {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 0;
    }

    .error {
        color: $error;
        margin: 0 0 1 0;
    }

    .thinking-label {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .thinking-body {
        color: $text-muted;
        padding: 0 2;
    }

    .tool-call-label {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .tool-call {
        color: $text-muted;
        padding: 0 2;
    }

    .response {
        margin: 1 0 0 0;
    }

    .interrupted {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .notice {
        color: $text-muted;
        margin: 1 0 0 0;
    }

    .mascot {
        color: $accent;
        text-align: center;
        margin: 1 0 0 0;
    }
    """

    BINDINGS = [
        ("ctrl+c", "interrupt", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("escape", "abort_response", "Abort"),
        ("ctrl+o", "toggle_tool_call_detail", "Detail"),
    ]
    COMMANDS = App.COMMANDS | {
        InitCommandProvider, ModelCommandProvider, SessionCommandProvider, ThemeCommandProvider,
        ThinkingCommandProvider, TrustWorkspaceCommandProvider,
    }

    def __init__(
        self,
        session: Session | None = None,
        process_config: ProcessConfig | None = None,
        initial_message: str | None = None,
        session_log_enabled: bool = True,
        trust_manager: TrustManager | None = None,
        config_flag_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._process_config = process_config or ProcessConfig()
        if session is None:
            new_config = self._process_config.session.model_copy()
            session = Session(
                new_config,
                process_config=self._process_config,
                tool_registry=ToolRegistry(self._process_config, new_config),
            )
        self._session = session
        self._initial_message = initial_message
        self._session_log_enabled = session_log_enabled
        self._trust_manager = trust_manager
        """Set only by a real `klorb.cli.main()` invocation — opts this app into resolving and
        (if needed) interactively bootstrapping workspace trust at startup, and into offering
        the "Trust workspace" palette command (see `docs/specs/projects-and-trust.md`). `None`
        (the default, and every existing test's constructor call) disables all of that: no
        startup prompts, no history announcement, no palette command — so nothing needs a real
        or fake `TrustManager`/`$KLORB_DATA_DIR` just to construct a `ReplApp`."""
        self._config_flag_path = config_flag_path
        """The `--config` path (if any) `klorb.cli.main()`'s initial `load_process_config()`
        call was given, threaded through so `_apply_workspace_config`'s later reload (once a
        workspace's trust/registration state changes) layers it back in identically."""
        self._cancel_event: threading.Event | None = None
        self._shell_cancel_event: threading.Event | None = None
        self._tool_call_widgets: list[ToolCallStatic] = []
        self._tool_call_detail_shown: bool = False
        self._history_pinned_to_bottom: bool = True
        if self._process_config.theme is not None and self._process_config.theme in self.available_themes:
            self.theme = self._process_config.theme
        self.title = "klorb"
        self.sub_title = self._session.config.model

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id=HISTORY_ID)
        yield PromptPalette(id=PROMPT_PALETTE_ID)
        yield PromptInput(placeholder="Send a message...", id=PROMPT_INPUT_ID)
        with Horizontal(id="status-row"):
            yield PaletteHint(id=PALETTE_HINT_ID)
            yield Footer(show_command_palette=False)
            yield Static(id=STATUS_BAR_ID)

    def select_model(self, name: str) -> None:
        """Make `name` the active model used for subsequent prompts, the default model for any
        future session started in this process, and — persisted to the per-user config file
        (`sessionDefaults.model`) — the default for every future klorb process too.
        """
        self._session.config.model = name
        self._process_config.session.model = name
        persist_session_default(user_config_path(), "model", name)
        self.sub_title = name
        self._update_status_bar()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Model set to {name}.", classes="notice"))

    def show_notice(self, message: str, *, error: bool = False) -> None:
        """Append `message` to the history scroll as a `.notice` (or `.error` if `error`)
        item. Exposed for callers outside `ReplApp` (e.g. `InitCommandProvider`) that report a
        one-off status/result but shouldn't otherwise depend on history-widget internals like
        `HISTORY_ID`/`VerticalScroll`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(message, classes="error" if error else "notice"))

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Every default system command except "Theme" — `ThemeCommandProvider` supersedes it
        with a command that shows the current theme name in its own label and opens
        `ThemeSelectionScreen`'s `(*)`-marked picker instead of Textual's own unmarked one.
        """
        for command in super().get_system_commands(screen):
            if command.title != "Theme":
                yield command

    def get_current_theme_name(self) -> str:
        """Return the name of the currently-active Textual theme — see `ThemeCommandProvider`."""
        return self.theme

    def available_theme_names(self) -> list[str]:
        """Return every registered Textual theme name, sorted for a stable display order in
        `ThemeSelectionScreen` — see `ThemeCommandProvider`.
        """
        return sorted(self.available_themes)

    def select_theme(self, name: str) -> None:
        """Make `name` the active Textual theme, persist it to the per-user config file
        (`persist_theme`) so it's restored on the next klorb session, and announce the change
        in the history scroll.
        """
        self.theme = name
        self._process_config.theme = name
        persist_theme(name)
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Changed current theme to `{name}`.", classes="notice"))

    def format_title(self, title: str, sub_title: str) -> Content:
        """Compose the `Header`'s displayed title from the current workspace path (shortened
        per `_format_workspace_path` if it's too long to comfortably fit) plus an `"(Untrusted)"`
        marker when the workspace isn't trusted, followed by `sub_title` (the active model,
        kept in sync with it by `select_model`) and, if thinking is enabled, its effort level in
        parentheses — e.g. `".../path/to/somewhere (Untrusted) - gpt-4o (High)"`. Overrides
        `App.format_title`, which otherwise just joins `title`/`sub_title` with an em dash;
        `title` is ignored here since a bare "klorb" app name isn't useful once every session is
        tied to a specific workspace directory.
        """
        workspace = self._session.config.workspace
        workspace_display = _format_workspace_path(workspace.path)
        if not workspace.trusted:
            workspace_display += " (Untrusted)"
        model_display = sub_title
        if self.get_thinking_enabled():
            model_display += f" ({self.get_thinking_effort().title()})"
        return Content.assemble(
            Content(workspace_display),
            (" - ", "dim"),
            Content(model_display).stylize("dim"),
        )

    def _refresh_header_title(self) -> None:
        """Force the `Header` to redraw via `format_title()`, for state changes (workspace
        trust, thinking effort/enabled) that it depends on but that `Header` doesn't itself
        watch — unlike `self.title`/`self.sub_title`, whose own changes it watches directly.
        `mutate_reactive` re-runs those watchers with `self.sub_title`'s value unchanged, which
        is enough to make `Header` re-invoke `format_title()`.
        """
        self.mutate_reactive(ReplApp.sub_title)

    def get_thinking_effort(self) -> ThinkingEffort:
        """Return the reasoning effort level currently configured for subsequent prompts."""
        return self._session.config.thinking_effort

    def get_thinking_enabled(self) -> bool:
        """Return whether extended-thinking requests are currently enabled."""
        return self._session.config.thinking_enabled

    def set_thinking_enabled(self, enabled: bool) -> None:
        """Enable or disable extended-thinking requests for subsequent prompts, the default for
        any future session started in this process, and — persisted to the per-user config
        file (`sessionDefaults.thinking.enabled`) — the default for every future klorb process
        too.
        """
        self._session.config.thinking_enabled = enabled
        self._process_config.session.thinking_enabled = enabled
        persist_session_default(user_config_path(), "thinking.enabled", enabled)
        self._refresh_header_title()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Thinking {'enabled' if enabled else 'disabled'}.", classes="notice"))

    def set_thinking_effort(self, effort: ThinkingEffort) -> None:
        """Set the reasoning effort level used for subsequent prompts, when thinking is
        enabled and the active model supports it. Also becomes the default effort level for
        any future session started in this process, and — persisted to the per-user config
        file (`sessionDefaults.thinking.effort`) — the default for every future klorb process
        too.
        """
        self._session.config.thinking_effort = effort
        self._process_config.session.thinking_effort = effort
        persist_session_default(user_config_path(), "thinking.effort", effort)
        self._refresh_header_title()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Thinking effort set to {effort}.", classes="notice"))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide the `abort_response` binding from the footer unless a response is currently
        streaming in, since there's nothing for Escape to abort otherwise.
        """
        if action == "abort_response":
            return self._cancel_event is not None
        return True

    def action_abort_response(self) -> None:
        """Signal the in-flight turn's worker thread to stop consuming the response stream."""
        if self._cancel_event is not None:
            self._cancel_event.set()

    def action_interrupt(self) -> None:
        """Ctrl+C: if a `!`-prefixed shell command is currently running, terminate it instead
        of quitting — mirroring a terminal's own Ctrl+C, which interrupts the foreground job
        rather than closing the shell. With no shell command in flight, falls through to the
        ordinary quit behavior (matching `action_quit`, which Ctrl+C used to be bound to
        directly).
        """
        if self._shell_cancel_event is not None:
            self._shell_cancel_event.set()
        else:
            self.exit()

    def action_toggle_tool_call_detail(self) -> None:
        """Ctrl+O: flip every `ToolCallStatic` currently in the history — from any turn, not
        just the latest — between its one-line summary and its fuller detail view, all at once.

        Also updates the footer's own label for this binding — `"Detail"` while summaries are
        shown (offering to reveal detail), `"Hide"` once detail is shown (offering to go back)
        — since a binding's description is otherwise fixed at `BINDINGS` class-definition time.
        `self._bindings` is this `ReplApp` instance's own mutable copy of the merged class-level
        `BINDINGS` (see `DOMNode.__init__`), so replacing its `"ctrl+o"` entry only affects this
        instance; `refresh_bindings()` is Textual's documented hook for prompting `Footer` to
        redraw after such a change.
        """
        self._tool_call_detail_shown = not self._tool_call_detail_shown
        for widget in self._tool_call_widgets:
            widget.set_detail_shown(self._tool_call_detail_shown)
        label = "Hide" if self._tool_call_detail_shown else "Detail"
        self._bindings.key_to_bindings["ctrl+o"] = [
            Binding("ctrl+o", "toggle_tool_call_detail", label)]
        self.refresh_bindings()

    def on_mount(self) -> None:
        """Label and focus the input box, cap its growth at the configured max height, watch
        the history's scroll position (see `_on_history_scroll_changed`), show the initial
        `> palette` hint (the box starts empty), greet the user with the klorb mascot (see
        `MASCOT_ART`/`MASCOT_GREETING`), note in the history if no per-user config file exists
        yet (see `CONFIG_MISSING_MESSAGE`), then hand off to
        `_run_startup_workspace_and_initial_message` to resolve (and, if this is a brand-new
        workspace, interactively bootstrap) workspace trust before submitting any initial
        message as the first turn.
        """
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.border_title = "message"
        input_widget.styles.max_height = self._process_config.prompt_input_max_lines + 1
        input_widget.focus()
        self._update_status_bar()
        self._update_palette_hint()

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self.watch(history, "scroll_y", self._on_history_scroll_changed, init=False)

        history.mount(Static(f"{MASCOT_ART}\n\n{MASCOT_GREETING}", classes="mascot"))

        if not user_config_path().is_file():
            history.mount(Static(CONFIG_MISSING_MESSAGE, classes="notice"))

        self._run_startup_workspace_and_initial_message()

    @work()
    async def _run_startup_workspace_and_initial_message(self) -> None:
        """Runs as a proper (non-thread) Textual worker, not directly from `on_mount`, because
        `_resolve_workspace_trust()` may push a `ConfirmScreen` and await its dismissal
        (`push_screen_wait`) — which Textual only allows from within an active worker's
        context, not from a plain event handler. Submits `self._initial_message` (if any) only
        once workspace trust is resolved, so the very first turn already reflects any
        newly-granted permissions rather than racing the interactive bootstrap.
        """
        await self._resolve_workspace_trust()
        if self._initial_message:
            self._submit_prompt(self._initial_message)

    def workspace_trust_management_enabled(self) -> bool:
        """Whether this app was constructed with a `TrustManager` — see `TrustWorkspaceCommandProvider`."""
        return self._trust_manager is not None

    def is_workspace_trusted(self) -> bool:
        """Whether the current workspace is currently trusted — see `TrustWorkspaceCommandProvider`."""
        return self._session.config.workspace.trusted

    async def _resolve_workspace_trust(self) -> None:
        """A no-op unless this app was given a `TrustManager` (see `__init__`). Otherwise:
        if `SessionConfig.workspace` (already resolved by whichever
        `klorb.process_config.load_process_config()` call built the live session's config) has
        no `projects.json` record yet (`workspace.id is None`), interactively bootstraps it
        (`_bootstrap_new_workspace`) and applies whatever the user decided
        (`_apply_workspace_config`); either way, finishes by announcing the resulting trust
        state in the history (`_announce_workspace`). See docs/specs/projects-and-trust.md.
        """
        if self._trust_manager is None:
            return
        workspace = self._session.config.workspace
        if workspace.id is None:
            workspace = await self._bootstrap_new_workspace(workspace)
            self._apply_workspace_config(workspace)
        self._announce_workspace(workspace)

    async def _bootstrap_new_workspace(self, workspace: Workspace) -> Workspace:
        """Ask the two workspace-bootstrap questions from docs/specs/projects-and-trust.md for
        a workspace with no `projects.json` record yet: whether to open it as a project (a
        persistent record plus a starter `.klorb/klorb-config.json`), and whether to trust it.
        If opened as a project, registers it (`TrustManager.register_project`) and writes its
        starter config file (`write_initial_project_config`, burning in the session's
        currently-active model); otherwise returns an unregistered `Workspace` carrying only
        the trust decision, kept in memory for the rest of this session's lifetime (see
        `SessionConfig.workspace`).
        """
        assert self._trust_manager is not None
        open_as_project = await self.push_screen_wait(ConfirmScreen(
            f"You are working in {workspace.path}. Open as a project?\n\n"
            "Projects have persistent settings files and permissions.",
            yes_label="Open as project", no_label="Not now"))
        trusted = await self.push_screen_wait(
            ConfirmScreen(f"Do you trust the workspace at {workspace.path}?"))
        if open_as_project:
            new_workspace = self._trust_manager.register_project(workspace.path, trusted)
            write_initial_project_config(workspace.path, self._process_config.session.model, trusted)
            return new_workspace
        return Workspace(path=workspace.path, is_project=False, trusted=trusted)

    def _apply_workspace_config(self, workspace: Workspace) -> None:
        """Recompute the layered config now that `workspace`'s trust/registration state may
        have just changed (a newly-trusted project's own `.klorb/klorb-config.json` becomes
        readable, or a freshly-registered project's just-written starter file does), and apply
        it to the live process/session config in place — mutating the existing
        `ProcessConfig`/`SessionConfig` objects rather than reconstructing `Session`/
        `ToolRegistry`, so any conversation history already in this session is left untouched
        and every tool sees the change on its very next call (both hold references to these
        same objects, not copies — see `klorb.tools.registry.ToolRegistry`).

        `read_dirs`/`write_dirs` are concatenated onto the live session's own via
        `_concat_dir_rules` (never replaced), so an "Allow (this session)" grant made before
        the user decided to trust the workspace isn't discarded. Every process-only
        (`ProcessConfig`) field is overwritten from the reload outright; `session`'s other
        scalar fields (model, thinking, tool-call limits) are deliberately left alone here,
        since a config file's declared defaults shouldn't silently override a value the user
        may have already picked interactively earlier this same session.

        `workspace` itself is dual-written onto both the live `self._session.config` and the
        `self._process_config.session` template — the same pattern `select_model()`/
        `set_thinking_enabled()` already use for session-scoped settings — so a future `/clear`
        in this process inherits the resolved trust state instead of re-bootstrapping it.
        """
        reloaded = load_process_config(
            config_flag_path=self._config_flag_path, cwd=workspace.path, workspace=workspace)

        for field_name in ProcessConfig.model_fields:
            if field_name == "session":
                continue
            setattr(self._process_config, field_name, getattr(reloaded, field_name))

        self._session.config.workspace = workspace
        self._process_config.session.workspace = workspace

        self._session.config.read_dirs = _concat_dir_rules(
            self._session.config.read_dirs, reloaded.session.read_dirs)
        self._session.config.write_dirs = _concat_dir_rules(
            self._session.config.write_dirs, reloaded.session.write_dirs)
        self._process_config.session.read_dirs = _concat_dir_rules(
            self._process_config.session.read_dirs, reloaded.session.read_dirs)
        self._process_config.session.write_dirs = _concat_dir_rules(
            self._process_config.session.write_dirs, reloaded.session.write_dirs)

        self._refresh_header_title()

    def _announce_workspace(self, workspace: Workspace) -> None:
        """Mount the one-line history notice docs/specs/projects-and-trust.md specifies for the
        resulting workspace state: which directory, and whether it's trusted."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        if workspace.trusted:
            history.mount(Static(f"Working in project: {workspace.path}", classes="notice"))
        else:
            history.mount(Static(
                f"The workspace at {workspace.path} is not trusted. "
                f"Run `{PALETTE_PREFIX}{TRUST_WORKSPACE_LABEL}` to change this.",
                classes="notice"))

    @work()
    async def trust_workspace(self) -> None:
        """`TrustWorkspaceCommandProvider`'s action: confirm with the user, then trust the
        current workspace — persisting the decision to `projects.json` if it's a registered
        project (`TrustManager.set_trusted`) — and apply the now-unlocked config
        (`_apply_workspace_config`). If the workspace is a registered project with no
        `.klorb/klorb-config.json` of its own yet, additionally offers to write one from the
        live session's current settings (`write_session_defaults_to_project_config`), so any
        `readDirs`/`writeDirs` grants built up earlier this session aren't lost the next time
        klorb opens this workspace. A no-op if the user declines the initial confirmation, or
        if this app has no `TrustManager` (see `workspace_trust_management_enabled`) — the
        palette command that calls this is hidden in that case, but this method still guards
        against being invoked some other way.

        `@work()` (a proper Textual worker, not a thread) rather than a plain `async def`: like
        `_run_startup_workspace_and_initial_message`, this pushes a `ConfirmScreen` and awaits
        its dismissal (`push_screen_wait`), which Textual only allows from within an active
        worker's context. Called directly as the palette command's callback (see
        `TrustWorkspaceCommandProvider`/`PromptInput._run_palette_command`) — invoking a
        `@work()`-decorated method starts the worker and returns a `Worker`, not a coroutine, so
        callers don't (and shouldn't) await this directly.
        """
        if self._trust_manager is None:
            return
        workspace = self._session.config.workspace
        confirmed = await self.push_screen_wait(
            ConfirmScreen(f"Do you trust the workspace at {workspace.path}?"))
        if not confirmed:
            return

        trusted_workspace = workspace.model_copy(update={"trusted": True})
        if trusted_workspace.is_project:
            assert trusted_workspace.id is not None
            self._trust_manager.set_trusted(trusted_workspace.id, True)
        self._apply_workspace_config(trusted_workspace)

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"Trusted workspace {trusted_workspace.path}.", classes="notice"))

        config_path = project_config_path(trusted_workspace.path)
        if trusted_workspace.is_project and not config_path.is_file():
            init_confirmed = await self.push_screen_wait(ConfirmScreen(
                "Initialize the project config file with your current session settings?"))
            if init_confirmed:
                write_session_defaults_to_project_config(trusted_workspace.path, self._session.config)
                history.mount(Static(f"Wrote project config to {config_path}.", classes="notice"))

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Keep the `> palette` hint in sync with the prompt input's content, including edits
        that don't go through a key event (e.g. `PromptInput._recall_history`'s `self.text =
        ...` assignments).
        """
        self._update_palette_hint()

    def _update_palette_hint(self) -> None:
        """Show the `PaletteHint` only while the box is empty or holds just the leading `>`
        (i.e. before any real query narrows it) — the statusbar analog of the `Footer`'s own
        `^p` command-palette hint (suppressed via `Footer(show_command_palette=False)` in
        `compose()`, since palette-from-prompt is now the primary way to reach the command
        palette — see `docs/specs/command-palette-from-prompt.md`).
        """
        hint = self.query_one(f"#{PALETTE_HINT_ID}", PaletteHint)
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        text = prompt_input.text
        if text in ("", PALETTE_PREFIX):
            hint.show_hint()
        else:
            hint.hide_hint()

    def _on_history_scroll_changed(self) -> None:
        """Keep `_history_pinned_to_bottom` in sync with the history viewport's actual scroll
        position, recomputed only when Textual itself changes `scroll_y` — via our own
        `scroll_end()`/`scroll_home()` calls landing, or genuine user scrolling — rather than by
        re-reading `is_vertical_scroll_end` synchronously wherever a streaming update happens.

        Mounting a widget doesn't immediately update `max_scroll_y` (it's only remeasured on the
        next layout refresh, which is exactly why `scroll_end()`/`scroll_home()` themselves defer
        via `call_after_refresh` by default), so eagerly re-checking `is_vertical_scroll_end`
        right after scheduling one streaming update — before an earlier update's own deferred
        scroll has actually landed — could misread a still-pinned viewport as scrolled away.
        Reacting only to `scroll_y` actually changing sidesteps that: by the time this fires,
        `max_scroll_y` is necessarily consistent with the `scroll_y` that triggered it.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._history_pinned_to_bottom = _pinned_to_bottom(history)

    def _update_status_bar(self) -> None:
        """Refresh the status bar's running token tally against the active model's context window."""
        status_bar = self.query_one(f"#{STATUS_BAR_ID}", Static)
        used = format_token_count(self._session.total_tokens_used())
        limit = self._session.max_context_window()
        status_bar.update(used if limit is None else f"{used} / {format_token_count(limit)}")

    def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        """Echo the submitted prompt into the history and dispatch it to the model, or
        handle `:q`/`/quit`/`/exit` or a `!`-prefixed shell command synchronously.

        `PromptInput._execute_palette_hit` handles a palette selection (e.g. `>Clear
        session`) entirely on its own — invoking the hit's command directly rather than
        posting `Submitted` — so a `>`-prefixed `prompt_text` reaching here is always one
        that ruled out every palette option, dispatched as an ordinary prompt like any other
        (see `docs/specs/command-palette-from-prompt.md`).
        """
        prompt_text = event.value.strip()
        if not prompt_text:
            return

        event.prompt_input.text = ""
        if prompt_text in [":q", "/quit", "/exit"]:
            self.exit()
            return

        if prompt_text.startswith("!") and "\n" not in prompt_text and "\r" not in prompt_text:
            self._submit_shell_command(prompt_text[1:].lstrip())
            return

        self._submit_prompt(prompt_text)

    def _submit_shell_command(self, command: str) -> None:
        """Echo `!command` into the history, disable the input, and dispatch it to a worker
        thread (`_run_shell_command`) so a slow command can't block the UI. Only one shell
        command can be in flight at a time for this REPL: the input box stays disabled for the
        duration, exactly as it does for a model turn in `_submit_prompt`, so a user can't
        start a second one while the first is still running.
        """
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"!{command}", classes="prompt"))
        history.scroll_end(animate=False)

        self._shell_cancel_event = threading.Event()
        self.refresh_bindings()
        self._run_shell_command(command, self._shell_cancel_event)

    @work(thread=True)
    def _run_shell_command(self, command: str, cancel_event: threading.Event) -> None:
        """Run `command` via `UserShellCommand` on a worker thread, streaming its combined
        stdout/stderr into the history as it arrives (mirroring `_send_prompt`'s progressive
        rendering of a streamed model response). `output_lock` serializes the stdout and
        stderr pump threads `UserShellCommand.run()` spawns internally — both call back into
        `handle_output` concurrently, and without a lock two racing calls could each mount
        their own output widget, or one line could clobber another's addition to `accumulated`.

        The shell binary and timeout come from `ProcessConfig` (`shell_command`,
        `shell_timeout_seconds`); a timeout or a Ctrl+C interrupt (`action_interrupt`, which
        sets `cancel_event`) both kill the process and are reported as an error in the history
        rather than raised, matching how a failed/aborted model turn is displayed.
        """
        output_widget: Static | None = None
        accumulated = ""
        output_lock = threading.Lock()

        def handle_output(delta_text: str) -> None:
            nonlocal accumulated, output_widget
            with output_lock:
                accumulated += delta_text
                if output_widget is None:
                    output_widget = self.call_from_thread(self._mount_shell_output_widget, accumulated)
                else:
                    self.call_from_thread(
                        self._update_shell_output_widget, output_widget, escape(accumulated))

        error_message: str | None = None
        try:
            _, _, rc = UserShellCommand(command, shell_path=self._process_config.shell_command).run(
                on_stdout=handle_output, on_stderr=handle_output,
                timeout=self._process_config.shell_timeout_seconds, cancel_event=cancel_event)
        except ShellCommandTimedOut:
            timeout = self._process_config.shell_timeout_seconds
            error_message = f"Shell command timed out after {timeout:g}s; killed."
        except ShellCommandCancelled:
            error_message = "Shell command interrupted."
        else:
            if rc != 0:
                error_message = f"Shell command exited with status {rc}."

        self.call_from_thread(self._finish_shell_command, error_message)

    def _mount_shell_output_widget(self, initial_text: str) -> Static:
        """Mount a new `Static` widget for a streaming shell command's output and return it.

        Uses `Static` rather than the `Markdown` widget `_mount_response_widget` mounts for a
        streaming model reply: shell output is plain text, not markdown, and `Markdown`'s
        CommonMark rendering collapses a single newline within a paragraph into a soft line
        break (a space), which mangles multi-line command output. `Static` renders text
        verbatim, preserving every newline. `initial_text` is escaped so literal `[`/`]` in the
        output (e.g. `[INFO]` log tags) can't be misread as Rich console markup.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Static(escape(initial_text))
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _update_shell_output_widget(self, widget: Static, text: str) -> None:
        """Update a streaming shell command's output `Static` widget with the latest
        accumulated `text` (already escaped), following the view to the bottom only if it was
        already pinned there before this growth (see `_history_pinned_to_bottom`) — so a user
        who's scrolled up to reread earlier output isn't yanked back down by every incoming
        chunk.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)

    def _finish_shell_command(self, error_message: str | None) -> None:
        """Show `error_message` (if any) in the history, then finish the shell command's
        "turn" exactly like a model turn (`_finish_turn`): scroll to the end, refresh the
        token tally, and re-enable/refocus the input box.
        """
        if error_message is not None:
            self._show_error(error_message)
        else:
            history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
            self._finish_turn(history, self._history_pinned_to_bottom)

    def clear_session(self) -> None:
        """Replace the active Session with a fresh one (new id, config reset to the current
        process-level template), reset the visible history, and roll over the per-session
        log file if session logging is enabled for this REPL invocation.
        """
        new_config = self._process_config.session.model_copy()
        self._session = Session(
            new_config,
            provider=self._session.provider,
            model_registry=self._session.model_registry,
            process_config=self._process_config,
            tool_registry=ToolRegistry(self._process_config, new_config),
        )

        if self._session_log_enabled:
            log_path = session_log_path(self._session.id)
            configure_logging(repl_mode=True, log_path=log_path)
            logger.debug("Cleared session; now logging to %s", log_path)

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.remove_children()
        history.mount(Static("Session cleared.", classes="notice"))

        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.clear_input_history()
        prompt_input.focus()
        self._update_status_bar()

    def _submit_prompt(self, prompt_text: str) -> None:
        """Echo `prompt_text` into the history, disable the input, and dispatch it."""
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = Static(prompt_text, classes="prompt")
        history.mount(prompt_widget)
        history.scroll_end(animate=False)

        self._cancel_event = threading.Event()
        self.refresh_bindings()
        self._send_prompt(prompt_text, self._cancel_event)

    @work(thread=True)
    def _send_prompt(self, prompt_text: str, cancel_event: threading.Event) -> None:
        """Send `prompt_text` to the model on a worker thread so the UI stays responsive.

        Renders the response progressively as chunks stream in: a `Markdown` widget is
        mounted on the first chunk and updated on each subsequent one. Reasoning/thinking
        chunks, if the model sends any, are rendered the same way but behind a `<Thinking>`
        label and in italics, via a separate `Static` widget (not `Markdown`: reasoning text
        commonly spans multiple paragraphs, and Markdown's `*...*` emphasis syntax doesn't
        apply across blank-line-separated blocks, whereas Rich's `[italic]...[/italic]`
        console markup styles every line regardless).

        A turn with tool calls is really a sequence of rounds under the hood (see
        `Session._dispatch_turn`): each round streams its own thinking/response text, then, if
        it ends in a tool-call request, `Session` dispatches those calls before starting the
        next round's stream. `on_tool_call` fires once per finished call, in between two
        rounds' streams, so seeing it is what marks a round boundary here — `round_index` is
        bumped on every such call, and `handle_chunk`/`handle_thinking_chunk` compare it
        against the round their current widget belongs to, starting a fresh widget rather than
        appending to the previous round's whenever it's moved on. Without this, one round's
        widget would keep absorbing every later round's text too, reading as a single
        ever-growing block that swallows the tool calls mounted in between rather than as
        separate, time-ordered blocks.

        If `cancel_event` is set (via Escape / `action_abort_response`) before the response
        finishes, `Session.send_turn()` raises `ResponseAborted`; the echoed prompt and any
        widgets already mounted for this turn's partial response/thinking/tool calls are left
        in place (`Session` keeps the same content in history, tagged `"aborted"` — see
        `_handle_aborted_response`) rather than torn down, and the input box is left empty for
        the next message rather than repopulated with this one. `response_widget`/
        `thinking_widget` at that point necessarily belong to the round that was still
        streaming when Escape fired, matching the single round's worth of content `Session`
        keeps for it.
        """
        response_widget: Markdown | None = None
        accumulated = ""
        response_round: int | None = None
        thinking_widget: Static | None = None
        thinking_accumulated = ""
        thinking_round: int | None = None
        round_index = 0

        def handle_chunk(delta_text: str) -> None:
            nonlocal accumulated, response_widget, response_round
            if response_round != round_index:
                response_widget = None
                accumulated = ""
                response_round = round_index
            accumulated += delta_text
            if response_widget is None:
                response_widget = self.call_from_thread(self._mount_response_widget, accumulated)
            else:
                self.call_from_thread(self._update_response_widget, response_widget, accumulated)

        def handle_thinking_chunk(delta_text: str) -> None:
            nonlocal thinking_accumulated, thinking_widget, thinking_round
            if thinking_round != round_index:
                thinking_widget = None
                thinking_accumulated = ""
                thinking_round = round_index
            thinking_accumulated += delta_text
            if thinking_widget is None:
                thinking_widget, _ = self.call_from_thread(
                    self._mount_thinking_widget, thinking_accumulated)
            else:
                self.call_from_thread(
                    self._update_thinking_widget, thinking_widget, _italicized(thinking_accumulated))

        def handle_tool_call(event: ToolCallEvent) -> None:
            nonlocal round_index
            summary_text, detail_text = self._render_tool_call(event)
            self.call_from_thread(self._mount_tool_call_widget, summary_text, detail_text)
            round_index += 1

        try:
            response_text = self._session.send_turn(prompt_text, TurnEventHandlers(
                on_chunk=handle_chunk, on_thinking_chunk=handle_thinking_chunk,
                cancel_event=cancel_event, on_tool_call_limit_reached=self._on_tool_call_limit_reached,
                on_permission_ask=self._on_permission_ask, on_tool_call=handle_tool_call))
        except ResponseAborted:
            self.call_from_thread(
                self._handle_aborted_response, response_widget, accumulated,
                thinking_widget, thinking_accumulated)
        except Exception as exc:
            self.call_from_thread(self._show_error, str(exc))
        else:
            if response_widget is not None:
                self.call_from_thread(self._finalize_streamed_response, response_widget, response_text)
            else:
                self.call_from_thread(self._show_response, response_text)

    def _mount_response_widget(self, initial_text: str) -> Markdown:
        """Mount a new `Markdown` widget for a streaming response and return it."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Markdown(initial_text, classes="response")
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _update_response_widget(self, widget: Markdown, text: str) -> None:
        """Update a streaming response `Markdown` widget with the latest accumulated `text`,
        following the view to the bottom only if it was already pinned there before this change
        (see `_history_pinned_to_bottom`), so a user who's scrolled up to reread earlier output
        isn't yanked back down by every incoming chunk. `widget` is always still the last thing
        mounted in the history at this point: `_send_prompt` starts a fresh widget rather than
        reusing this one once a round boundary (a tool call) has passed, so there's nothing
        mounted after it left to get stuck behind.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)

    def _mount_thinking_widget(self, initial_text: str) -> tuple[Static, Static]:
        """Mount a left-justified `<Thinking>` label followed by an italicized `Static`
        widget for a streaming thinking block, and return `(body_widget, label_widget)`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(THINKING_LABEL, classes="thinking-label")
        history.mount(label_widget)
        widget = Static(_italicized(initial_text), classes="thinking-body")
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget, label_widget

    def _update_thinking_widget(self, widget: Static, italicized_text: str) -> None:
        """Update a streaming `<Thinking>` `Static` widget with `italicized_text` (already run
        through `_italicized`), following the view to the bottom only if it was already pinned
        there before this change (see `_update_response_widget`) — the label mounted alongside
        `widget` never changes after being set, so only the body needs updating here.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(italicized_text)
        self._scroll_if_pinned(history, was_pinned)

    def _render_tool_call(self, event: ToolCallEvent) -> tuple[str, str]:
        """Render `event` as `(summary_text, detail_text)` via its tool's `summary()`/
        `detail_view()` — instantiating a fresh `Tool` purely to call these pure methods
        (`ToolRegistry.instantiate_tool()` is already a cheap, no-shared-state operation; see
        docs/adrs/tool-registry-instantiates-a-fresh-tool-per-call.md) — or via the shared
        default formatters if `event.name` isn't a registered tool (e.g. a hallucinated tool
        name, which `Session._run_tool_calls` already turned into `event.error`).
        """
        registry = self._session.tool_registry
        try:
            if registry is None:
                raise KeyError(event.name)
            tool = registry.instantiate_tool(event.name)
        except KeyError:
            return (default_tool_call_summary(event.name, event.args, event.error),
                    default_tool_call_detail(event.name, event.args, event.result, event.error))
        return (tool.summary(event.args, event.result, event.error),
                tool.detail_view(event.args, event.result, event.error))

    def _mount_tool_call_widget(self, summary_text: str, detail_text: str) -> tuple[ToolCallStatic, Static]:
        """Mount a left-justified `<Tool use>` label (styled via the `.tool-call-label` CSS
        class, matching `<Thinking>`'s label/body split in `_mount_thinking_widget`) followed
        by a `ToolCallStatic` for one finished tool call, and return `(widget, label_widget)`.
        Applies the current global detail-shown state (see `action_toggle_tool_call_detail`)
        so a call rendered while detail view is already active shows detail immediately rather
        than a summary the user would have to toggle past.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(TOOL_USE_LABEL, classes="tool-call-label")
        history.mount(label_widget)
        widget = ToolCallStatic(summary_text, detail_text)
        widget.set_detail_shown(self._tool_call_detail_shown)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._tool_call_widgets.append(widget)
        return widget, label_widget

    async def _confirm_tool_call_limit(self, message: str) -> bool:
        """Show `ToolCallLimitScreen` with `message` and wait for the user's yes/no answer.

        Must be run on the app's own event loop, since it awaits the screen's dismissal —
        `_on_tool_call_limit_reached` is what the worker thread actually calls, via
        `call_from_thread`, to get here.
        """
        return await self.push_screen_wait(ToolCallLimitScreen(message))

    def _on_tool_call_limit_reached(self, message: str) -> bool:
        """`Session`'s `on_tool_call_limit_reached` callback: block the worker thread running
        `Session.send_turn()` until the user answers `ToolCallLimitScreen(message)`.
        """
        # mypy can't solve App.call_from_thread's `CallThreadReturnType` TypeVar against a
        # `Callable[[str], Coroutine[Any, Any, bool]]` argument (a stub-modeling limitation,
        # not a real type error: Textual's own runtime `invoke()` awaits coroutine callbacks
        # like any other `Callable[..., T | Awaitable[T]]` argument, and this is exercised by
        # test_tui_repl.py's ToolCallLimitScreen tests).
        callback = self._confirm_tool_call_limit
        confirmed: bool = self.call_from_thread(callback, message)  # type: ignore[arg-type]
        return confirmed

    async def _confirm_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        """Show `PermissionAskScreen` for `ask_ctx` and wait for the user's choice.

        Must be run on the app's own event loop, since it awaits the screen's dismissal —
        `_on_permission_ask` is what the worker thread actually calls, via `call_from_thread`,
        to get here. `compute_grant_paths()` is recomputed here (rather than threaded in from
        wherever `ask_ctx` was built) purely so the modal can show the directory a persistent
        grant would actually cover; it's pure and read-only, so calling it again inside
        `_on_permission_ask`/`apply_permission_grant` afterwards is safe and cheap.
        """
        granted_paths = compute_grant_paths(
            self._session.config.read_dirs, self._session.config.write_dirs,
            self._session.config.workspace.path, ask_ctx.path, ask_ctx.is_write)
        return await self.push_screen_wait(PermissionAskScreen(ask_ctx, granted_paths))

    def _on_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        """`Session`'s `on_permission_ask` callback: block the worker thread running
        `Session.send_turn()` until the user answers `PermissionAskScreen`, then return that
        choice as-is. Applying (and, for "workspace"/"homedir", persisting to disk) any grant
        the choice implies is `Session`'s own responsibility —
        `Session._retry_after_permission_decision`, via
        `klorb.permissions.grant.apply_permission_grant` — using the `ProcessConfig` reference
        `ReplApp` gave it at construction time; this callback only needs to surface the user's
        decision, not act on it.
        """
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_permission_ask
        decision: PermissionDecision = self.call_from_thread(callback, ask_ctx)  # type: ignore[arg-type]
        return decision

    def _finalize_streamed_response(self, widget: Markdown, response_text: str) -> None:
        """Reconcile a streamed `Markdown` widget with the final response and finish the turn."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(response_text)
        self._finish_turn(history, was_pinned)

    def _show_response(self, response_text: str) -> None:
        """Append a model response to the history and re-enable the input box."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Markdown(response_text, classes="response"))
        self._finish_turn(history, was_pinned)

    def _show_error(self, message: str) -> None:
        """Append an error message to the history and re-enable the input box."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Static(f"Error: {message}", classes="error"))
        self._finish_turn(history, was_pinned)

    def _handle_aborted_response(
        self, response_widget: Markdown | None, response_text: str,
        thinking_widget: Static | None, thinking_text: str,
    ) -> None:
        """Leave the echoed prompt and every widget mounted for the aborted turn in place
        (`Session` keeps the same content in history, tagged `processing_state="aborted"`,
        rather than discarding it), tagging whichever of the response/thinking widgets was
        still streaming when Escape fired with an "(interrupted)" marker. If neither had
        started streaming yet (the turn was interrupted before its first chunk, e.g. during an
        earlier round's tool-call dispatch), mount a standalone marker instead so the
        interruption is still visible. The input box is left empty rather than repopulated
        with the original prompt, since that prompt is now a real, if incomplete, turn in
        history rather than a draft to resend.
        """
        if response_widget is not None:
            response_widget.update(f"{response_text}\n\n*(interrupted)*")
        elif thinking_widget is not None:
            thinking_widget.update(_italicized(f"{thinking_text}\n\n(interrupted)"))
        else:
            history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
            history.mount(Static("(interrupted)", classes="interrupted"))

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._finish_turn(history, self._history_pinned_to_bottom)

    def _scroll_if_pinned(self, history: VerticalScroll, was_pinned: bool) -> None:
        """Scroll `history` to its end iff `was_pinned` — `_history_pinned_to_bottom`'s value
        captured immediately before the content that just changed was applied — so streaming
        updates follow the user to the bottom only when they hadn't already scrolled away from
        it.
        """
        if was_pinned:
            history.scroll_end(animate=False)

    def _finish_turn(self, history: VerticalScroll, was_pinned: bool) -> None:
        """Scroll the history into view (iff `was_pinned`, see `_scroll_if_pinned`), refresh the
        token tally, and hand focus back to the input box. Also clears the in-flight turn's
        cancel event and pending-prompt tracking (model turn) and the shell command's cancel
        event (`_run_shell_command`), since neither Escape nor Ctrl+C has anything left to
        abort/interrupt once a turn is done (successfully, in error, or aborted) — shared by
        both a model turn and a shell command, since only one of the two is ever in flight at a
        time.
        """
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = False
        input_widget.focus()
        self._cancel_event = None
        self._shell_cancel_event = None
        self.refresh_bindings()


def run_repl(
    session: Session | None = None,
    process_config: ProcessConfig | None = None,
    initial_message: str | None = None,
    session_log_enabled: bool = True,
    trust_manager: TrustManager | None = None,
    config_flag_path: Path | None = None,
) -> None:
    """Launch the interactive klorb REPL, optionally submitting `initial_message` first."""
    ReplApp(
        session=session,
        process_config=process_config,
        initial_message=initial_message,
        session_log_enabled=session_log_enabled,
        trust_manager=trust_manager,
        config_flag_path=config_flag_path,
    ).run()
