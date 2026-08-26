# © Copyright 2026 Aaron Kimball
"""SidebarMixin: keeps the docked right-hand sidebar panels mutually exclusive."""

from klorb.tui._base import ReplAppBase
from klorb.tui.constants import SIDEBAR_PANEL_IDS


class SidebarMixin(ReplAppBase):
    """Owns the one piece of logic every sidebar-panel toggle shares: hiding whichever other
    panel is currently showing before its own opens."""

    def _hide_other_sidebars(self, except_name: str) -> None:
        """Hide the currently active sidebar panel, unless it's already `except_name`."""
        active = self._active_sidebar
        if active is not None and active != except_name:
            self.query_one(f"#{SIDEBAR_PANEL_IDS[active]}").display = False
