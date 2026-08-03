# © Copyright 2026 Aaron Kimball
"""StatusBarMixin: status-bar token tallies, the palette hint, and the permission-
framework badge for ReplApp."""

from textual.containers import VerticalScroll
from textual.widgets import Static, TextArea

from klorb.tui._base import ReplAppBase
from klorb.tui.constants import (
    HISTORY_ID,
    OUTPUT_TOKENS_ID,
    PALETTE_HINT_ID,
    PERMISSION_BADGE_ID,
    PERMISSION_FRAMEWORK_CYCLE,
    PROMPT_INPUT_ID,
    SESSION_NAME_ID,
    STATUS_BAR_ID,
)
from klorb.tui.formatting import format_token_count, pinned_to_bottom
from klorb.tui.widgets.palette import PALETTE_PREFIX
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.status_widgets import PaletteHint, PermissionBadge


class StatusBarMixin(ReplAppBase):
    """Status-bar token tallies, palette-mode hint, and permission-framework badge -- see
    `ReplApp` for how this mixes into the concrete app class."""

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
        self._history_pinned_to_bottom = pinned_to_bottom(history)

    def _update_status_bar(self) -> None:
        """Refresh both footer token tallies -- context usage vs. the model's context window, and
        total model-output tokens -- for whichever session is currently selected
        (`_selected_session`, the root session by default), not necessarily the root session
        itself: switching the subagents panel's selection calls this too (see
        `klorb.tui.mixins.subagents_panel.SubagentsPanelMixin._select_session`/
        `_append_new_subagent_messages`), so the footer always reflects the transcript actually
        on screen.
        """
        session = self._selected_session
        status_bar = self.query_one(f"#{STATUS_BAR_ID}", Static)
        used = format_token_count(session.total_tokens_used())
        limit = session.max_context_window()
        if limit is None:
            status_bar.update(f"\u2191 {used}")
        else:
            status_bar.update(f"\u2191 {used} / {format_token_count(limit)}")

        output_tokens = self.query_one(f"#{OUTPUT_TOKENS_ID}", Static)
        output_tokens.update(f"\u2193 {format_token_count(session.total_output_tokens_used())}")

    def _update_session_name_line(self, text: str) -> None:
        """Set the `SESSION_NAME_ID` line to `"Session: <text>"` -- called by
        `PromptSubmissionMixin._handle_session_name_changed` once the first-turn classifier
        resolves (`text` is the derived title) or fails (`text` is instead
        `klorb.session_naming.fallback_session_title`'s derivation, already applied to
        `Session.name` by `SessionCoreMixin._run_session_naming`). `clear_session()`/
        `_maybe_restore_latest_session()` instead reset the line directly to `NEW_SESSION_LABEL`
        (no `"Session: "` prefix), matching `compose()`'s own initial value.
        """
        session_name = self.query_one(f"#{SESSION_NAME_ID}", Static)
        session_name.update(f"Session: {text}")

    def _update_permission_badge(self) -> None:
        """Set the permission badge to show `Session.config.permission_framework`
        (`[ask]`/`[auto]`/`[deny]`), so the user always knows whether tool-permission asks
        are being shown interactively, auto-approved, or auto-denied. Called once from
        `on_mount()`, with no flash -- `action_cycle_permission_framework()` is what changes
        the value (and flashes the badge) after startup.
        """
        badge = self.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        badge.set_value(self._session.config.permission_framework)

    def action_cycle_permission_framework(self) -> None:
        """Advance `Session.config.permission_framework` to the next value in
        `PERMISSION_FRAMEWORK_CYCLE` (wrapping around), and flash the badge to draw the eye
        to the change. Bound to Shift+Tab as a `priority=True` app-level binding (see
        `ReplApp.BINDINGS`): a priority binding is checked from the `App` down before the key
        event is forwarded to whatever widget currently has focus (see Textual's
        `App._check_bindings`), so this fires regardless of focus -- including while
        `PromptInput` is disabled and blurred during an in-flight turn or an open interaction
        panel, exactly when a user is most likely to want to flip the framework (e.g. to
        `"auto"` so an about-to-be-asked permission just proceeds). Goes through
        `Session.set_permission_framework()`, not a direct assignment, so the model is told
        about the change via a system-harness interjection prepended to the next turn — see
        docs/specs/permissions.md's "Permission framework change interjection" section.
        """
        current = self._session.config.permission_framework
        next_index = (PERMISSION_FRAMEWORK_CYCLE.index(current) + 1) % len(PERMISSION_FRAMEWORK_CYCLE)
        next_value = PERMISSION_FRAMEWORK_CYCLE[next_index]
        self._session.set_permission_framework(next_value)
        badge = self.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        badge.flash_to(next_value)

    def on_permission_badge_clicked(self, event: "PermissionBadge.Clicked") -> None:
        """`PermissionBadge` posts this when clicked; hand off to
        `action_cycle_permission_framework()` — the mouse equivalent of the Shift+Tab
        binding."""
        self.action_cycle_permission_framework()
