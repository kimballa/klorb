# © Copyright 2026 Aaron Kimball
"""PromptInput: the multi-line prompt box widget."""

import inspect
from pathlib import Path

from textual import events
from textual.actions import SkipAction
from textual.command import DiscoveryHit, Hit
from textual.message import Message
from textual.types import IgnoreReturnCallbackType
from textual.widgets import TextArea

from klorb.tui.widgets.file_finder import (
    FILE_FINDER_ID,
    FileFinderPanel,
    FinderMatch,
    MentionQuery,
    build_mention_insertion,
    detect_mention_query,
    escape_mention_path,
    filter_workspace_files,
)
from klorb.tui.widgets.palette import PALETTE_PREFIX, PROMPT_PALETTE_ID, PromptPalette, gather_palette_hits
from klorb.tui.widgets.skill_finder import (
    SKILL_FINDER_ID,
    SkillFinderPanel,
    SkillMatch,
    SkillQuery,
    detect_skill_query,
    filter_skills,
)
from klorb.workspace.input_history import append_history, load_history


class PromptInput(TextArea):
    """A multi-line prompt box that soft-wraps long input and grows with it.

    Enter submits the current text. Ctrl+Enter inserts a literal newline instead. Shift+Enter
    and Alt+Enter aren't usable for this: Shift+Enter is indistinguishable from plain Enter on
    terminals without kitty keyboard protocol support (confirmed by testing: both report as plain
    `"enter"`), and Alt+Enter is claimed by Windows/many terminal emulators for toggling
    fullscreen before it ever reaches the app. Ctrl+Enter itself commonly arrives as `"ctrl+j"`
    rather than `"ctrl+enter"`, since terminals typically send the same linefeed control byte for
    both Ctrl+Enter and Ctrl+J; both spellings are handled here.

    Up-arrow at the start of the text and down-arrow at the end of the text walk a per-session
    history of previously-submitted prompts, loading the recalled entry into the box for editing
    and resending; once that walk has started, further up/down presses keep walking regardless of
    where the cursor lands. Walking up from an untouched draft stashes that draft's text so
    walking back down past the most recent entry restores it rather than clearing the box. Any
    text-mutating action (typing, deleting, pasting) detaches the box from its current position
    in that history so the now-edited text is treated as a fresh draft rather than a rooted
    recall; pure cursor movement (arrow keys, home/end, selection) does not detach. Clearing the
    session resets the recall position and the stashed draft while preserving the in-memory
    history entries.

    Whenever the text starts with `>`, up/down/enter instead drive the inline `PromptPalette`
    popup mounted just above this widget rather than history recall or submission.

    Whenever the cursor sits inside an `@mention` with at least one matching workspace file,
    up/down/enter/tab/escape instead drive the inline `FileFinderPanel` popup. Checked before
    palette mode, so a `@mention` inside a `>`-prefixed palette query still opens the finder.
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

    # Binding-driven text mutations: each deletes, cuts, pastes, or
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

        Done here rather than in `__init__` so the widget keeps `TextArea`'s own
        constructor signature untouched. `TextArea` defines `_on_mount`, not `on_mount`, so
        this handler is purely additive and doesn't shadow one of its own.
        """
        self._history: list[str] = []
        self._history_index: int | None = None
        self._draft: str = ""
        self._palette_dismissed: bool = False
        self._suppress_palette_during_recall: bool = False
        self._last_key: str | None = None
        self._history_path: Path | None = None
        self._finder_mention: MentionQuery | None = None
        """The `@mention` (if any) the cursor currently sits inside of, recomputed on every
        text/selection change by `refresh_finder`."""
        self._finder_matches: list[FinderMatch] = []
        """Workspace files and directories currently matching `_finder_mention`'s query."""
        self._finder_dismissed_at: tuple[int, int] | None = None
        """The `(row, start_column)` of the `@mention` Escape last dismissed the finder popup
        for."""
        self._skill_query: SkillQuery | None = None
        """The `/skill` query (if any) the cursor currently sits inside of, recomputed on every
        text/selection change by `refresh_skill_finder`."""
        self._skill_matches: list[SkillMatch] = []
        """Skills currently matching `_skill_query`'s query."""
        self._skill_dismissed_at: tuple[int, int] | None = None
        """The `(row, start_column)` of the `/skill` query Escape last dismissed the skill
        finder popup for."""
        # Reverse-incremental-search state (Ctrl+R). When `_isearch_active` is True, typed
        # printable characters extend `_isearch_query` and each extension re-runs a
        # newest-first, case-insensitive substring search of `self._history`, loading the
        # match into the box. Enter/Escape or any
        # non-printable navigation exits the search, leaving the current match in the box
        # (Enter leaves it as a draft to edit/resend; Escape would restore the pre-search
        # draft, but for simplicity both just commit the match).
        self._isearch_active: bool = False
        self._isearch_query: str = ""
        self._isearch_match_index: int | None = None

    def set_history_store(self, path: Path | None) -> None:
        """Point this widget at the on-disk `history` file for the current project and seed
        `self._history` from it so up/down-arrow recall reaches prompts submitted in earlier
        klorb sessions in the same folder. `path is None` disables file-backed persistence
        entirely, which is what a `ReplApp`
        without a resolved workspace wants so tests never touch a real `$KLORB_DATA_DIR`.

        Seeding happens here, exactly once at startup, rather than in `on_mount` because the
        workspace isn't resolved yet at `on_mount` time. `clear_session` does not re-seed
        because `clear_input_history` preserves the in-memory history; the entries loaded
        here remain available through a session clear.
        """
        self._history_path = path
        if path is not None:
            self._history = load_history(path)
            self._history_index = None

    def clear_input_history(self) -> None:
        """Reset the recall position and navigation state while preserving the in-memory
        prompt history.

        The recall position, stashed draft, and
        palette/isearch state are reset for a fresh session, while the history entries
        loaded from the on-disk `history` file at startup remain available via up/down-arrow.
        The on-disk `history` file itself is never touched here: it's an append-only shared
        log that multiple klorb instances may be writing to concurrently.
        """
        self._history_index = None
        self._draft = ""
        self._palette_dismissed = False
        self._suppress_palette_during_recall = False
        self._exit_isearch()
        self._palette_widget().hide()
        self._finder_mention = None
        self._finder_matches = []
        self._finder_dismissed_at = None
        self._finder_widget().hide()
        self._skill_query = None
        self._skill_matches = []
        self._skill_dismissed_at = None
        self._skill_finder_widget().hide()

    @property
    def _palette_mode(self) -> bool:
        """Whether up/down/enter/escape should drive the `PromptPalette` popup rather than
        history recall or submission: the text starts with `>` and hasn't been dismissed out
        of palette mode for this draft, and the text didn't just get there via history recall
        (`_suppress_palette_during_recall`).
        """
        return (
            self.text.startswith(PALETTE_PREFIX)
            and not self._palette_dismissed
            and not self._suppress_palette_during_recall
        )

    def _palette_widget(self) -> PromptPalette:
        """The sibling `PromptPalette` popup mounted just above this widget."""
        return self.screen.query_one(f"#{PROMPT_PALETTE_ID}", PromptPalette)

    @property
    def _file_finder_mode(self) -> bool:
        """Whether up/down/enter/tab/escape should drive the `FileFinderPanel` popup rather
        than palette mode, history recall, or submission: the cursor sits inside an `@mention`
        (`_finder_mention`, kept current by `refresh_finder`) that currently has at least one
        matching workspace file. A mention with no matches (including one `_dismiss_finder` has
        just hidden) doesn't claim these keys.
        """
        return self._finder_mention is not None and bool(self._finder_matches)

    def _finder_widget(self) -> FileFinderPanel:
        """The sibling `FileFinderPanel` popup mounted just above this widget."""
        return self.screen.query_one(f"#{FILE_FINDER_ID}", FileFinderPanel)

    def _current_mention(self) -> MentionQuery | None:
        """The `@mention` (if any) the cursor currently sits inside of, on its own line."""
        row, column = self.cursor_location
        return detect_mention_query(self.document[row], column)

    def refresh_finder(self) -> None:
        """Recompute the active `@mention` at the cursor and update the finder popup to match.
        Called (via `call_later`) on every text change and cursor/selection movement.

        A mention whose start position is `_finder_dismissed_at` (the user just pressed Escape
        on it) stays closed even if its query would otherwise match, until the cursor moves to a
        different mention.
        """
        mention = self._current_mention()
        self._finder_mention = mention
        if mention is None:
            self._finder_matches = []
            self._finder_dismissed_at = None
            self._finder_widget().hide()
            return
        row, _ = self.cursor_location
        if (row, mention.start_column) == self._finder_dismissed_at:
            self._finder_matches = []
            self._finder_widget().hide()
            return
        self._finder_dismissed_at = None
        self._finder_matches = filter_workspace_files(self._workspace_files(), mention.query)
        finder = self._finder_widget()
        if self._finder_matches:
            finder.show_matches(self._finder_matches)
        else:
            finder.hide()

    def _workspace_files(self) -> list[str]:
        """The active `ReplApp`'s current `@`-mention file list, or `[]` if this widget isn't
        mounted under one or no file index has been started."""
        from klorb.tui.app import ReplApp  # breaks a circular import: ReplApp composes PromptInput

        if not isinstance(self.app, ReplApp):
            return []
        return self.app.workspace_files()

    @property
    def _skill_finder_mode(self) -> bool:
        """Whether up/down/enter/tab/escape should drive the `SkillFinderPanel` popup rather
        than file finder mode, palette mode, history recall, or submission: the cursor sits
        inside a `/skill` query that currently has at least one matching skill."""
        return self._skill_query is not None and bool(self._skill_matches)

    def _skill_finder_widget(self) -> SkillFinderPanel:
        """The sibling `SkillFinderPanel` popup mounted just above this widget."""
        return self.screen.query_one(f"#{SKILL_FINDER_ID}", SkillFinderPanel)

    def _current_skill_query(self) -> SkillQuery | None:
        """The `/skill` query (if any) the cursor currently sits inside of, on its own line."""
        row, column = self.cursor_location
        return detect_skill_query(self.document[row], column)

    def refresh_skill_finder(self) -> None:
        """Recompute the active `/skill` query at the cursor and update the finder popup."""
        query = self._current_skill_query()
        self._skill_query = query
        if query is None:
            self._skill_matches = []
            self._skill_dismissed_at = None
            self._skill_finder_widget().hide()
            return
        row, _ = self.cursor_location
        if (row, query.start_column) == self._skill_dismissed_at:
            self._skill_matches = []
            self._skill_finder_widget().hide()
            return
        self._skill_dismissed_at = None
        self._skill_matches = filter_skills(self._discoverable_skills(), query.query)
        finder = self._skill_finder_widget()
        if self._skill_matches:
            finder.show_matches(self._skill_matches)
        else:
            finder.hide()

    def _discoverable_skills(self) -> list[SkillMatch]:
        """The active `ReplApp`'s discoverable skills as `SkillMatch` objects, or `[]` if
        this widget isn't mounted under one or no session is active."""
        from klorb.tui.app import ReplApp  # breaks a circular import

        if not isinstance(self.app, ReplApp):
            return []
        return self.app.discoverable_skill_matches()

    def _dismiss_skill_finder(self) -> None:
        """Leave skill finder mode without changing the prompt's text."""
        if self._skill_query is None:
            return
        row, _ = self.cursor_location
        self._skill_dismissed_at = (row, self._skill_query.start_column)
        self._skill_matches = []
        self._skill_finder_widget().hide()

    def select_skill_match(self) -> None:
        """Apply the skill finder's currently-highlighted match: replace the `/query` span
        with `/<skill_name> ` and close the popup. A no-op if there's no active query or no
        highlighted row."""
        query = self._skill_query
        widget = self._skill_finder_widget()
        match = widget.current_match
        if query is None or match is None:
            return
        row, column = self.cursor_location
        assert isinstance(match, SkillMatch)
        insertion = f"/{match.name} "
        self.replace(insertion, (row, query.start_column), (row, column))
        self._skill_query = None
        self._skill_matches = []
        self._skill_dismissed_at = None
        widget.hide()

    def _dismiss_finder(self) -> None:
        """Leave finder mode for the mention at the cursor without changing the prompt's text,
        so the user can keep typing it as plain text (Escape's behavior). Recorded by mention
        start position, not just "dismissed": moving to a different `@mention` (or starting a
        new one) reopens the popup normally."""
        if self._finder_mention is None:
            return
        row, _ = self.cursor_location
        self._finder_dismissed_at = (row, self._finder_mention.start_column)
        self._finder_matches = []
        self._finder_widget().hide()

    def select_finder_match(self) -> None:
        """Apply the finder's currently-highlighted match. A file match replaces the active
        `@query` with an escaped `@mention` (per
        `klorb.tui.widgets.file_finder.escape_mention_path`) and closes the popup; a directory
        match instead narrows the query to that directory (with a trailing `/`) and leaves the
        popup open so the user can keep drilling in, since a directory isn't a valid mention
        target on its own. A no-op if there's no active mention or no highlighted row."""
        mention = self._finder_mention
        match = self._finder_widget().current_match
        if mention is None or match is None:
            return
        assert isinstance(match, FinderMatch)
        row, column = self.cursor_location
        if match.is_dir:
            insertion = f"@{escape_mention_path(match.path)}/"
            self.replace(insertion, (row, mention.start_column), (row, column))
            return
        insertion, _ = build_mention_insertion(mention.start_column, column, match.path)
        self.replace(insertion, (row, mention.start_column), (row, column))
        self._finder_mention = None
        self._finder_matches = []
        self._finder_dismissed_at = None
        self._finder_widget().hide()

    def action_copy(self) -> None:
        """Copy this box's own text selection to the clipboard, same as the base `TextArea.
        action_copy`, but also records the press for `ReplApp.action_interrupt`'s Ctrl+C streak
        bookkeeping (`ReplApp._note_ctrl_c_copy`). Raises
        `SkipAction`, exactly like the base implementation, when nothing is selected, so the
        binding chain falls through past this widget unchanged.
        """
        from klorb.tui.app import ReplApp  # breaks a circular import: ReplApp composes PromptInput

        selected_text = self.selected_text
        if not selected_text:
            raise SkipAction()
        self.app.copy_to_clipboard(selected_text)
        if isinstance(self.app, ReplApp):
            self.app._note_ctrl_c_copy()

    async def _on_key(self, event: events.Key) -> None:
        """Submit on Enter; insert a newline on Ctrl+Enter; walk the input history on
        up/down at the text boundaries; detach from history on any text mutation; defer
        everything else (typing, navigation, selection) to `TextArea`'s own handling; and,
        while `_palette_mode` is active, let up/down/enter/escape drive the `PromptPalette`
        popup instead of any of the above. `_file_finder_mode` is checked first, so up/down/enter/tab/
        escape drive the `FileFinderPanel` popup instead whenever the cursor sits inside a
        matching `@mention`.

        `self._last_key` records the key currently being processed for `on_text_area_changed`
        to read: a mutation-binding key like backspace or delete is applied via a `TextArea`
        action that Textual dispatches once this whole event — bubbling past
        `_on_key` up to the `App`'s own binding check — has finished, which in practice lands
        even later than a `call_after_refresh`/`call_later` callback scheduled from here would
        run. So `_on_key` can't reliably read the *post*-edit `self.text` for those keys no
        matter how it defers; only `TextArea.Changed` (posted once the edit has actually
        landed, regardless of what caused it) can, which is why `_refresh_palette` is now
        driven from `on_text_area_changed` instead of being called directly from here.
        """
        key = event.key
        self._last_key = key
        if self._isearch_active:
            # While a reverse-i-search is in progress, the keystroke vocabulary narrows to
            # what readline accepts mid-search: printable characters extend the query,
            # Ctrl+R advances to the next-older match, Enter/Escape commit the current match
            # and exit, and everything else (arrows, home/end, deletion bindings, ...) also
            # exits, leaving the match in the box to edit or resend. Ctrl+H (backspace's
            # control-byte form) shrinks the query.
            event.stop()
            event.prevent_default()
            if key == "escape" or key == "enter":
                self._exit_isearch()
                if key == "enter":
                    self._record_and_submit()
                return
            if key == "ctrl+r":
                match_idx = self._isearch_match_index
                start = match_idx if match_idx is not None else len(self._history)
                self._isearch_step(start)
                return
            if key in ("ctrl+h", "backspace"):
                if self._isearch_query:
                    self._isearch_query = self._isearch_query[:-1]
                    self._isearch_step(len(self._history))
                return
            if event.is_printable and event.character:
                self._isearch_query += event.character
                self._isearch_step(len(self._history))
                return
            self._exit_isearch()
            return
        if key == "ctrl+r":
            event.stop()
            event.prevent_default()
            self._enter_isearch()
            return
        if self._file_finder_mode:
            if key == "escape":
                event.stop()
                event.prevent_default()
                self._dismiss_finder()
                return
            if key == "up":
                event.stop()
                event.prevent_default()
                self._finder_widget().move_highlight(-1)
                return
            if key == "down":
                event.stop()
                event.prevent_default()
                self._finder_widget().move_highlight(1)
                return
            if key in ("enter", "tab"):
                event.stop()
                event.prevent_default()
                self.select_finder_match()
                return
        if self._skill_finder_mode:
            if key == "escape":
                event.stop()
                event.prevent_default()
                self._dismiss_skill_finder()
                return
            if key == "up":
                event.stop()
                event.prevent_default()
                self._skill_finder_widget().move_highlight(-1)
                return
            if key == "down":
                event.stop()
                event.prevent_default()
                self._skill_finder_widget().move_highlight(1)
                return
            if key in ("enter", "tab"):
                event.stop()
                event.prevent_default()
                self.select_skill_match()
                return
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
        """Detach from history before a paste inserts text, since pasting mutates the box.

        Deliberately does *not* call `super()._on_paste(event)`. Textual dispatches an event to
        every `_on_paste` it finds walking the widget's MRO, so `TextArea._on_paste` -- which does the actual
        insertion -- already runs on its own, right after this override, unless a handler calls
        `event.prevent_default()` to stop the walk. `TextArea._on_paste` never does, so calling
        `super()._on_paste(event)` here would insert the pasted text a second time (the classic
        Ctrl+V-pastes-twice bug). The MRO ordering (subclass first) still guarantees this
        detach runs before the base handler mutates the box.
        """
        self._last_key = None
        self._detach_from_history()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Refresh palette state whenever the text actually changes, regardless of what
        changed it. `TextArea` posts this exactly when a mutation actually lands, which sidesteps the
        ordering problem `_on_key` has for anything applied via a bound action: rather than
        guessing when that action has run, just react to the signal that says it just did.
        A recalled entry is the one case that must *not* trigger a palette refresh despite
        changing the text, so it's checked first and skipped entirely.
        """
        if self._suppress_palette_during_recall:
            return
        self.call_later(self._refresh_palette, self._last_key)
        self.call_later(self.refresh_finder)
        self.call_later(self.refresh_skill_finder)

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        """Refresh finder state whenever the cursor moves without the text itself changing --
        arrow keys, Home/End, a click -- so moving into or out of an `@mention`'s span reopens
        or closes the popup even though `on_text_area_changed` (which also refreshes it, for
        the text-mutating case) won't fire. Skipped during history recall for the same reason
        `on_text_area_changed` skips its own refresh there."""
        if self._suppress_palette_during_recall:
            return
        self.call_later(self.refresh_finder)
        self.call_later(self.refresh_skill_finder)

    async def _refresh_palette(self, key: str | None) -> None:
        """Recompute palette mode from the current text and update the popup to match.

        Text that no longer starts with `>` always resets `_palette_dismissed` and hides the
        popup, so a fresh `>`
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
        command once this key event has finished being handled (`call_later`, since a command
        that itself pushes a modal shouldn't do so mid-keystroke). `_run_palette_command`
        records the selection into the input history only after `hit.command` has actually run.
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
        `>Clear session` must still leave `>Clear session` recallable afterward, but `clear_session()`
        resets the history via `PromptInput.clear_input_history()` as part of starting a
        fresh session. Appending here, after that reset has already happened, is what makes
        the entry survive as the new history's first (and, until something else is
        submitted, only) entry.
        """
        result = command()
        if inspect.isawaitable(result):
            await result
        entry = f"{PALETTE_PREFIX}{canonical_text}"
        self._history.append(entry)
        self._history_index = None
        self._persist_history_entry(entry)

    def _detach_from_history(self) -> None:
        """Mark the current text as no longer rooted at a position in the input history, so a
        subsequent up/down at the boundaries resumes recall from the most recent entry rather
        than continuing from a now-stale index. Also lifts a recalled entry's suppression of
        palette mode, since editing it turns it into a fresh draft that
        a leading `>` should once again be read as a live palette query.
        """
        self._history_index = None
        self._suppress_palette_during_recall = False
        self._exit_isearch()

    def _persist_history_entry(self, entry: str) -> None:
        """Append `entry` to the on-disk `history` file when one has been attached; a no-op
        otherwise. The file is opened in append mode and never rewritten, so concurrent klorb
        instances in the same folder each just append their own most-recent message."""
        if self._history_path is not None:
            append_history(self._history_path, entry)

    def _enter_isearch(self) -> None:
        """Begin a Ctrl+R reverse-incremental-search: stash the current draft (so a later
        Escape can restore it), reset the query, and search the history for the empty query."""
        self._isearch_active = True
        self._isearch_query = ""
        self._isearch_match_index = None
        self._draft = self.text
        self.border_title = "(history)"
        self._isearch_step(0)

    def _isearch_step(self, start_index: int) -> None:
        """Run one newest-first, case-insensitive substring search of `self._history` for
        `_isearch_query`, beginning just *before* `start_index` (so repeated presses of Ctrl+R
        without changing the query advance to the next-older match), and load the match — if
        any — into the box. No match leaves the box showing the (partial) query."""
        if not self._history:
            self._isearch_match_index = None
            self.text = self._isearch_query
            self.move_cursor(self._last_location())
            return
        query = self._isearch_query.casefold()
        # Walk newest-first, starting at the index just before `start_index` so a fresh
        # query (start_index=0) finds the newest match and a repeated Ctrl+R advances.
        i = start_index - 1
        while i >= 0:
            if query in self._history[i].casefold():
                self._isearch_match_index = i
                self._suppress_palette_during_recall = True
                self.text = self._history[i]
                self.move_cursor(self._last_location())
                return
            i -= 1
        self._isearch_match_index = None
        self.text = self._isearch_query
        self.move_cursor(self._last_location())

    def _exit_isearch(self) -> None:
        """Leave reverse-i-search mode, keeping whatever match (or typed query) is currently
        in the box as an ordinary editable draft. Does not restore the pre-search draft: in
        readline, Enter accepts the match while Escape cancels back to the draft, but klorb
        commits the match in both cases for simplicity."""
        if not self._isearch_active:
            return
        self._isearch_active = False
        self._isearch_query = ""
        self._isearch_match_index = None
        self.border_title = "message"

    def _record_and_submit(self) -> None:
        """Record the current (non-empty) text into the input history and post `Submitted`.

        The recall position is reset to a fresh draft so the next up-arrow walks back from the
        just-appended entry.
        """
        value = self.text
        if not value.strip():
            self.post_message(self.Submitted(self, value))
            return
        self._history.append(value)
        self._persist_history_entry(value)
        self._history_index = None
        self.post_message(self.Submitted(self, value))

    def _recall_history(self, direction: int) -> bool:
        """Move the recall position by `direction` (-1 for up/older, +1 for down/newer) and load
        the entry there into the box, returning whether a recall actually happened.

        With no recorded history there's nothing to recall. Otherwise up-arrow from a fresh
        draft (`_history_index is None`) stashes the draft's current text in `_draft` and jumps
        to the most recent entry, then to older ones; down-arrow from the most recent entry
        restores that stashed draft rather than clearing to empty. Recalling loads the entry's
        text verbatim and lands the cursor at its end so the user can append to it. Loading a recalled
        entry also sets `_suppress_palette_during_recall` so a recalled entry starting with `>`
        is browsed as plain text rather than
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
