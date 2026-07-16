# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.panels.escalate_privileges_panel.EscalatePrivilegesPanel."""

from unittest.mock import MagicMock

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from klorb.session import EscalatePrivilegesContext, EscalatePrivilegesDecision
from klorb.tui.panels.escalate_privileges_panel import (
    ESCALATE_PRIVILEGES_HEADER_ID,
    ESCALATE_PRIVILEGES_ROW_APPROVE_ID,
    ESCALATE_PRIVILEGES_ROW_DENY_ID,
    ESCALATE_PRIVILEGES_TEXT_ID,
    EscalatePrivilegesPanel,
)


def _ctx(*, scope: str = "workspace", description: str = "Grant write access.") -> EscalatePrivilegesContext:
    return EscalatePrivilegesContext(scope=scope, description=description)


def _find_child(container: object, widget_id: str) -> object:
    return next(
        widget for widget in container._pending_children  # type: ignore[attr-defined]
        if widget.id == widget_id)


# --- unit-level (compose() without mounting) ---


def test_header_names_the_requested_scope() -> None:
    panel = EscalatePrivilegesPanel(_ctx(scope="workspace"))

    body = next(iter(panel.compose()))
    header = _find_child(body, ESCALATE_PRIVILEGES_HEADER_ID)

    assert isinstance(header, Static)
    assert str(header.render()) == "Privilege escalation requested · scope: workspace"


def test_body_shows_the_context_description() -> None:
    panel = EscalatePrivilegesPanel(_ctx(description="Grant write access to /etc."))

    body = next(iter(panel.compose()))
    text = _find_child(body, ESCALATE_PRIVILEGES_TEXT_ID)

    assert isinstance(text, Static)
    assert str(text.render()) == "Grant write access to /etc."


def test_action_move_row_wraps_around_between_approve_and_deny() -> None:
    panel = EscalatePrivilegesPanel(_ctx())
    panel._refresh_selection = MagicMock()  # type: ignore[method-assign]

    panel.action_move_row(-1)

    assert panel._row == 1  # wrapped from row 0 (Approve) to row 1 (Deny)


def test_action_confirm_on_approve_row_dismisses_approved() -> None:
    panel = EscalatePrivilegesPanel(_ctx())
    panel.dismiss = MagicMock()  # type: ignore[method-assign]

    panel.action_confirm()

    panel.dismiss.assert_called_once_with(EscalatePrivilegesDecision(approved=True))


def test_action_confirm_on_deny_row_dismisses_not_approved() -> None:
    panel = EscalatePrivilegesPanel(_ctx())
    panel.dismiss = MagicMock()  # type: ignore[method-assign]
    panel._row = 1

    panel.action_confirm()

    panel.dismiss.assert_called_once_with(EscalatePrivilegesDecision(approved=False))


def test_action_deny_dismisses_not_approved_regardless_of_selected_row() -> None:
    panel = EscalatePrivilegesPanel(_ctx())
    panel.dismiss = MagicMock()  # type: ignore[method-assign]
    panel._row = 0

    panel.action_deny()

    panel.dismiss.assert_called_once_with(EscalatePrivilegesDecision(approved=False))


def test_dismiss_with_no_callback_is_a_no_op() -> None:
    """`dismiss()` is only ever meaningfully wired up by `ReplApp` -- a standalone panel (as
    every test above constructs) has no `on_dismiss` callback and must not raise."""
    panel = EscalatePrivilegesPanel(_ctx())

    panel.dismiss(EscalatePrivilegesDecision(approved=False))


# --- mounted, through a real App (needs Pilot for key handling) ---


class _EscalatePrivilegesTestApp(App[None]):
    """Minimal standalone harness mounting a real `EscalatePrivilegesPanel` directly onto the
    screen (rather than through `ReplApp`'s `#interaction-panel`) so tests can drive its key
    bindings through Textual's `Pilot` without needing a full `ReplApp`/`Session`."""

    def __init__(self, ctx: EscalatePrivilegesContext) -> None:
        super().__init__()
        self._ctx = ctx
        self.decision: EscalatePrivilegesDecision | None = None

    def compose(self) -> ComposeResult:
        yield Vertical()

    async def on_mount(self) -> None:
        panel = EscalatePrivilegesPanel(self._ctx, on_dismiss=self._set_decision)
        await self.query_one(Vertical).mount(panel)

    def _set_decision(self, decision: EscalatePrivilegesDecision) -> None:
        self.decision = decision


async def test_approve_row_is_selected_on_mount() -> None:
    app = _EscalatePrivilegesTestApp(_ctx())

    async with app.run_test() as pilot:
        await pilot.pause()

        panel = app.query_one(EscalatePrivilegesPanel)
        approve_row = app.query_one(f"#{ESCALATE_PRIVILEGES_ROW_APPROVE_ID}", Static)
        deny_row = app.query_one(f"#{ESCALATE_PRIVILEGES_ROW_DENY_ID}", Static)
        assert panel.has_focus
        assert "selected" in approve_row.classes
        assert "selected" not in deny_row.classes


async def test_enter_on_approve_row_dismisses_approved() -> None:
    app = _EscalatePrivilegesTestApp(_ctx())

    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()

        assert app.decision == EscalatePrivilegesDecision(approved=True)


async def test_down_then_enter_dismisses_not_approved() -> None:
    app = _EscalatePrivilegesTestApp(_ctx())

    async with app.run_test() as pilot:
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

        assert app.decision == EscalatePrivilegesDecision(approved=False)


async def test_escape_denies_regardless_of_selected_row() -> None:
    app = _EscalatePrivilegesTestApp(_ctx())

    async with app.run_test() as pilot:
        await pilot.press("down")
        await pilot.press("escape")
        await pilot.pause()

        assert app.decision == EscalatePrivilegesDecision(approved=False)
