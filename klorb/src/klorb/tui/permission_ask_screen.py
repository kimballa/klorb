# © Copyright 2026 Aaron Kimball
"""Modal shown when a tool call hits an `"ask"` permission verdict (see
`klorb.session.PermissionAskContext`/`PermissionDecision` and
docs/specs/permissions.md's "Interactive ask confirmation" section)."""

from pathlib import Path
from typing import Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from klorb.session import PermissionAskContext, PermissionDecision

_Action = Literal["allow", "deny"]
_Scope = Literal["once", "session", "workspace", "homedir"]

_ACTIONS: tuple[_Action, ...] = ("allow", "deny")
"""Grid columns, left to right — indexed by the screen's `_column` cursor."""
_SCOPES: tuple[_Scope, ...] = ("once", "session", "workspace", "homedir")
"""Grid rows, top to bottom — indexed by the screen's `_row` cursor."""

_ACTION_LABELS: dict[_Action, str] = {"allow": "Allow", "deny": "Deny"}
_SCOPE_LABELS: dict[_Scope, str] = {
    "once": "Once",
    "session": "This session",
    "workspace": "Always, this workspace",
    "homedir": "Always, for me",
}

PERMISSION_ASK_GRID_ID = "permission-ask-grid"
PERMISSION_ASK_INPUT_ID = "permission-ask-other-input"
PERMISSION_ASK_OTHER_CELL_ID = "permission-ask-cell-other"

_OTHER_ROW = len(_SCOPES)
"""The grid's last row index — one row past the last real `_SCOPES` entry — occupied by a
single cell spanning both columns (`PERMISSION_ASK_OTHER_CELL_ID`) rather than an
action/scope pair; see `PermissionAskScreen._refresh_selection`/`action_confirm`."""
_TOTAL_ROWS = len(_SCOPES) + 1
"""`_SCOPES`'s rows plus the trailing `_OTHER_ROW` — the modulus `action_move_row` cycles
`_row` through."""


def _cell_id(column: int, row: int) -> str:
    return f"permission-ask-cell-{column}-{row}"


class PermissionAskScreen(ModalScreen[PermissionDecision]):
    """Presents a 2D grid of `_ACTIONS` (columns: Allow, Deny) by `_SCOPES` (rows: once,
    session, workspace, homedir) that the user navigates with arrow keys — Left/Right cycles
    the Allow/Deny column, Up/Down cycles the row — and confirms with Enter, dismissing
    `PermissionDecision(action=_ACTIONS[column], scope=_SCOPES[row])`. Escape is a fast path to
    an outright `PermissionDecision(action="deny", scope="once")` without needing to navigate
    there first.

    The grid's last row (`_OTHER_ROW`) is a single cell spanning both columns
    (`PERMISSION_ASK_OTHER_CELL_ID`), reachable the same way as any other row (pressing Down
    repeatedly from any column) since it isn't tied to a specific action. Confirming it with
    Enter — or pressing `o` directly, a fast path that works regardless of the current
    cursor position — reveals a free-text `Input` in place of the grid, for a response the
    grid's fixed cells can't express; submitting it (Enter) dismisses with
    `PermissionDecision(action="deny", scope="once", other_text=...)`.

    `granted_paths`/`granted_command_patterns` are `klorb.permissions.grant.compute_grant_paths()`/
    `klorb.permissions.command_grant.compute_command_grant_patterns()`'s pre-computed results —
    the directory (or directories)/command pattern(s) a persistent Allow would actually be
    recorded at — so the modal's copy can name the real scope of the grant up front: per the
    design decision behind this feature, an unmentioned path is always granted at its
    *containing directory* (never the single file the model happens to be touching right now),
    and an unmentioned command is granted at its exact argv. At most one of the two is set,
    mirroring `PermissionAskContext`'s own `path`/`command` mutual exclusivity; neither is set
    for a structural item, which has no persistable rule at any scope but `"once"`.

    `initial_action`/`initial_scope` seed the starting cursor position — `klorb.tui.repl.ReplApp`
    threads through the previous prompt's final selection here (see
    `ReplApp._last_permission_selection`) when several asks are shown back-to-back for one
    compound tool call, so the user doesn't have to re-navigate to the same spot for every item.
    """

    CSS = """
    PermissionAskScreen {
        align: center middle;
    }

    PermissionAskScreen Vertical {
        width: auto;
        max-width: 84;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }

    #permission-ask-message {
        margin: 0 0 1 0;
    }

    #permission-ask-grid {
        grid-size: 2 5;
        grid-gutter: 0 1;
        width: 65;
        height: auto;
        margin: 0 0 1 0;
    }

    #permission-ask-grid Static {
        width: 32;
        height: 1;
        padding: 0 1;
        content-align: left middle;
    }

    #permission-ask-grid Static#permission-ask-cell-other {
        column-span: 2;
        width: 100%;
    }

    #permission-ask-grid Static.selected {
        background: $accent;
        color: $text;
        text-style: bold;
    }

    #permission-ask-hint {
        color: $text-muted;
    }

    #permission-ask-other-input {
        width: 65;
    }
    """

    BINDINGS = [
        Binding("left", "move_column(-1)", "Allow", show=False),
        Binding("right", "move_column(1)", "Deny", show=False),
        Binding("up", "move_row(-1)", "Up", show=False),
        Binding("down", "move_row(1)", "Down", show=False),
        Binding("enter", "confirm", "Confirm"),
        Binding("escape", "decline", "Deny"),
        Binding("o", "other", "Other..."),
    ]

    def __init__(
        self, ask_ctx: PermissionAskContext, *,
        granted_paths: list[Path] | None = None,
        granted_command_patterns: list[list[str]] | None = None,
        initial_action: _Action = "allow",
        initial_scope: _Scope = "once",
    ) -> None:
        super().__init__()
        self._ask_ctx = ask_ctx
        self._granted_paths = granted_paths
        self._granted_command_patterns = granted_command_patterns
        self._column = _ACTIONS.index(initial_action)
        self._row = _SCOPES.index(initial_scope)

    def compose(self) -> ComposeResult:
        cells: list[Static] = [
            Static(f"{_ACTION_LABELS[action]} — {_SCOPE_LABELS[scope]}", id=_cell_id(column, row))
            for row, scope in enumerate(_SCOPES)
            for column, action in enumerate(_ACTIONS)
        ]
        cells.append(Static("Other...", id=PERMISSION_ASK_OTHER_CELL_ID))
        yield Vertical(
            Static(self._message_text(), id="permission-ask-message"),
            Grid(*cells, id=PERMISSION_ASK_GRID_ID),
            Static(
                "←/→ Allow/Deny   ↑/↓ scope   Enter confirm   "
                "O other   Esc deny",
                id="permission-ask-hint",
            ),
            id="permission-ask-body",
        )

    def on_mount(self) -> None:
        self._refresh_selection()

    def _message_text(self) -> str:
        lines = [f"Permission requested: {self._ask_ctx.resource_description}"]
        if self._granted_paths:
            directories = ", ".join(str(path) for path in self._granted_paths)
            lines.append(
                "\nAny persistent Allow choice below grants access to the whole directory:\n"
                f"{directories}")
        elif self._granted_command_patterns:
            patterns = ", ".join(" ".join(pattern) for pattern in self._granted_command_patterns)
            lines.append(f"\nAny persistent Allow choice below grants: {patterns}")
        return "\n".join(lines)

    def _refresh_selection(self) -> None:
        grid = self.query_one(f"#{PERMISSION_ASK_GRID_ID}", Grid)
        for row in range(len(_SCOPES)):
            for column in range(len(_ACTIONS)):
                cell = grid.query_one(f"#{_cell_id(column, row)}", Static)
                cell.set_class(column == self._column and row == self._row, "selected")
        other_cell = grid.query_one(f"#{PERMISSION_ASK_OTHER_CELL_ID}", Static)
        other_cell.set_class(self._row == _OTHER_ROW, "selected")

    def action_move_column(self, delta: int) -> None:
        self._column = (self._column + delta) % len(_ACTIONS)
        self._refresh_selection()

    def action_move_row(self, delta: int) -> None:
        self._row = (self._row + delta) % _TOTAL_ROWS
        self._refresh_selection()

    def action_confirm(self) -> None:
        if self._row == _OTHER_ROW:
            self._reveal_other_input()
            return
        self.dismiss(PermissionDecision(action=_ACTIONS[self._column], scope=_SCOPES[self._row]))

    def action_decline(self) -> None:
        self.dismiss(PermissionDecision(action="deny", scope="once"))

    def action_other(self) -> None:
        self._reveal_other_input()

    def _reveal_other_input(self) -> None:
        self.query_one(f"#{PERMISSION_ASK_GRID_ID}", Grid).remove()
        self.query_one("#permission-ask-hint", Static).remove()
        other_input = Input(
            placeholder="Explain why, or what to allow instead...", id=PERMISSION_ASK_INPUT_ID)
        self.query_one("#permission-ask-body", Vertical).mount(other_input)
        other_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(PermissionDecision(action="deny", scope="once", other_text=event.value))
