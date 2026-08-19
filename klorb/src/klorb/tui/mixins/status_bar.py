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
    """Status-bar token tallies, palette-mode hint, and permission-framework badge."""

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        """Keep the `> palette` hint in sync with the prompt input's content."""
        self._update_palette_hint()

    def _update_palette_hint(self) -> None:
        """Show the `PaletteHint` only while the box is empty or holds just the leading `>`."""
        hint = self.query_one(f"#{PALETTE_HINT_ID}", PaletteHint)
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        text = prompt_input.text
        if text in ("", PALETTE_PREFIX):
            hint.show_hint()
        else:
            hint.hide_hint()

    async def _on_history_scroll_changed(self) -> None:
        """Keep `_history_pinned_to_bottom` in sync with the history viewport's actual scroll
        position, and collapse/expand chunks for the new viewport. A no-op once the app has
        started shutting down, since this can still fire after `#history` itself is gone."""
        if not self.is_running:
            return
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._history_pinned_to_bottom = pinned_to_bottom(history)
        await self._history_virtualizer.refresh_visibility()

    def _update_status_bar(self) -> None:
        """Refresh both footer token tallies for whichever session is currently selected."""
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
        """Set the `SESSION_NAME_ID` line to `"Session: <text>"`."""
        session_name = self.query_one(f"#{SESSION_NAME_ID}", Static)
        session_name.update(f"Session: {text}")

    def _update_permission_badge(self) -> None:
        """Set the permission badge to show `Session.config.permission_framework`."""
        badge = self.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        badge.set_value(self._session.config.permission_framework)

    def action_cycle_permission_framework(self) -> None:
        """Advance `Session.config.permission_framework` to the next value in
        `PERMISSION_FRAMEWORK_CYCLE` (wrapping around), and flash the badge."""
        current = self._session.config.permission_framework
        next_index = (PERMISSION_FRAMEWORK_CYCLE.index(current) + 1) % len(PERMISSION_FRAMEWORK_CYCLE)
        next_value = PERMISSION_FRAMEWORK_CYCLE[next_index]
        self._session.set_permission_framework(next_value)
        badge = self.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        badge.flash_to(next_value)

    def on_permission_badge_clicked(self, event: "PermissionBadge.Clicked") -> None:
        """`PermissionBadge` posts this when clicked; cycle the permission framework."""
        self.action_cycle_permission_framework()
