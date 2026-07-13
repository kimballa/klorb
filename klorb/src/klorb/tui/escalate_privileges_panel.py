# © Copyright 2026 Aaron Kimball
"""Panel shown in the history scroll when the `EscalatePrivileges` tool requests a session-only
privilege grant (see `klorb.session.EscalatePrivilegesContext`/`EscalatePrivilegesDecision` and
`klorb.tools.escalate_privileges`)."""

from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static

from klorb.session import EscalatePrivilegesContext, EscalatePrivilegesDecision

ESCALATE_PRIVILEGES_HEADER_ID = "escalate-privileges-header"
ESCALATE_PRIVILEGES_TEXT_ID = "escalate-privileges-text"
ESCALATE_PRIVILEGES_ROW_APPROVE_ID = "escalate-privileges-row-approve"
ESCALATE_PRIVILEGES_ROW_DENY_ID = "escalate-privileges-row-deny"

_APPROVE_ROW = 0
_DENY_ROW = 1

_SECTION_END_CLASS = "ask-section-end"


def format_escalate_privileges_decision(decision: EscalatePrivilegesDecision) -> str:
    """Render `decision` for the history-scroll record `ReplApp` leaves behind once an
    `EscalatePrivilegesPanel` is dismissed."""
    return "Approve" if decision.approved else "Deny"


class EscalatePrivilegesPanel(Vertical):
    """Presents one `EscalatePrivilegesContext`: a two-row, Up/Down-navigable choice between
    `Approve` and `Deny`. Confirming `Approve` dismisses with `EscalatePrivilegesDecision(
    approved=True)`; confirming `Deny` (or pressing Escape) dismisses with
    `EscalatePrivilegesDecision(approved=False)`.

    `ReplApp` mounts this into its full-width `#interaction-panel` container, below the history
    scroll and above the (disabled, visually muted) prompt input, rather than as a floating
    modal \u2014 see `klorb.tui.permission_ask_panel.PermissionAskPanel`'s docstring for the shared
    mechanics: `dismiss()` just invokes the `on_dismiss` callback given at construction, and
    `ReplApp` owns unmounting this panel and recording a permanent record of the exchange
    afterward.
    """

    can_focus = True

    DEFAULT_CSS = """
    EscalatePrivilegesPanel {
        width: 1fr;
        height: auto;
        border-top: solid $accent;
        padding: 1 2;
    }

    #escalate-privileges-body {
        width: 1fr;
        height: auto;
    }

    #escalate-privileges-header {
        color: $text-warning;
        text-style: bold;
        margin: 0 0 1 0;
    }

    #escalate-privileges-text {
        text-style: bold;
        width: 1fr;
    }

    .ask-section-end {
        margin: 0 0 1 0;
    }

    #escalate-privileges-list {
        width: 72;
        height: auto;
        margin: 0 0 1 0;
    }

    #escalate-privileges-list Static {
        width: 100%;
        height: auto;
        padding: 0 1;
    }

    #escalate-privileges-list Static.selected {
        background: $accent;
        color: $text;
        text-style: bold;
    }

    #escalate-privileges-hint {
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("up", "move_row(-1)", "Up", show=False),
        Binding("down", "move_row(1)", "Down", show=False),
        Binding("left", "move_row(-1)", "Left", show=False),
        Binding("right", "move_row(1)", "Right", show=False),
        Binding("enter", "confirm", "Confirm"),
        Binding("escape", "deny", "Deny"),
    ]

    def __init__(
        self, escalate_ctx: EscalatePrivilegesContext, *,
        on_dismiss: Callable[[EscalatePrivilegesDecision], None] | None = None,
    ) -> None:
        super().__init__()
        self._escalate_ctx = escalate_ctx
        self._row = _APPROVE_ROW
        self._on_dismiss = on_dismiss

    def compose(self) -> ComposeResult:
        widgets: list[Widget] = [Static(self.header_text(), id=ESCALATE_PRIVILEGES_HEADER_ID)]
        widgets.append(Static(
            self._escalate_ctx.description, id=ESCALATE_PRIVILEGES_TEXT_ID, classes=_SECTION_END_CLASS))
        widgets.append(Vertical(
            Static("Approve", id=ESCALATE_PRIVILEGES_ROW_APPROVE_ID),
            Static("Deny", id=ESCALATE_PRIVILEGES_ROW_DENY_ID),
            id="escalate-privileges-list"))
        widgets.append(Static(
            "\u2191/\u2193 select   Enter confirm   Esc deny",
            id="escalate-privileges-hint"))
        yield Vertical(*widgets, id="escalate-privileges-body")

    def on_mount(self) -> None:
        self._refresh_selection()
        self.focus()

    def header_text(self) -> str:
        return f"Privilege escalation requested \u00b7 scope: {self._escalate_ctx.scope}"

    def _row_widget_id(self, row: int) -> str:
        return (ESCALATE_PRIVILEGES_ROW_APPROVE_ID if row == _APPROVE_ROW
                else ESCALATE_PRIVILEGES_ROW_DENY_ID)

    def _refresh_selection(self) -> None:
        list_container = self.query_one("#escalate-privileges-list", Vertical)
        for row in (_APPROVE_ROW, _DENY_ROW):
            widget = list_container.query_one(f"#{self._row_widget_id(row)}", Static)
            widget.set_class(row == self._row, "selected")

    def action_move_row(self, delta: int) -> None:
        self._row = (self._row + delta) % 2
        self._refresh_selection()

    def action_confirm(self) -> None:
        self.dismiss(EscalatePrivilegesDecision(approved=(self._row == _APPROVE_ROW)))

    def action_deny(self) -> None:
        self.dismiss(EscalatePrivilegesDecision(approved=False))

    def dismiss(self, decision: EscalatePrivilegesDecision) -> None:
        """Report `decision` to whoever mounted this panel, via the `on_dismiss` callback given
        at construction \u2014 see `PermissionAskPanel.dismiss`'s docstring for the same shape.
        A no-op with no callback given."""
        if self._on_dismiss is not None:
            self._on_dismiss(decision)
