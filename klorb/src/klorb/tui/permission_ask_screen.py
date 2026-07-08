# © Copyright 2026 Aaron Kimball
"""Modal shown when a tool call hits an `"ask"` permission verdict (see
`klorb.session.PermissionAskContext`/`PermissionDecision` and
docs/specs/permissions.md's "Interactive ask confirmation" section)."""

from pathlib import Path
from typing import Callable, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
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

PERMISSION_ASK_HEADER_ID = "permission-ask-header"
PERMISSION_ASK_COMMAND_ID = "permission-ask-command"
PERMISSION_ASK_MORE_ID = "permission-ask-more"
PERMISSION_ASK_DETAIL_ID = "permission-ask-detail"
PERMISSION_ASK_GRANTED_ID = "permission-ask-granted"
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

_MAX_COMMAND_PREVIEW_LINES = 6
"""How many lines of a long `command_text` `PermissionAskScreen` shows inline before truncating
to a `[more...]` indicator (`PERMISSION_ASK_MORE_ID`) — see `_command_preview`. A command short
enough to display in full still gets the same `[more...]` indicator if `ask_ctx.is_compound` is
set — see `PermissionAskScreen.compose`."""

_SECTION_END_CLASS = "ask-section-end"
"""CSS class carrying the trailing blank-line margin between one body section (header, command
preview, detail, granted-directory info) and the next — applied to whichever widget actually
ends a section, since that varies (the command preview itself if it fits inline, or the
`[more...]` indicator instead if it's truncated) rather than being fixed at compose() time."""


def _cell_id(column: int, row: int) -> str:
    return f"permission-ask-cell-{column}-{row}"


def _command_preview(command_text: str) -> tuple[str, bool]:
    """Return `(preview_text, truncated)`: the first `_MAX_COMMAND_PREVIEW_LINES` lines of
    `command_text` (unchanged, with `truncated=False`, if it's no longer than that already), for
    `PermissionAskScreen` to show inline instead of a command that might run to hundreds of lines
    (a heredoc-embedded script, say) — the full text is always still reachable via
    `ExpandedCommandScreen`."""
    lines = command_text.splitlines() or [""]
    if len(lines) <= _MAX_COMMAND_PREVIEW_LINES:
        return command_text, False
    return "\n".join(lines[:_MAX_COMMAND_PREVIEW_LINES]), True


class _MoreIndicator(Static):
    """The `[more...]` affordance shown below a truncated command preview: clicking it calls
    `on_activate` (`PermissionAskScreen.action_expand_command`) to push `ExpandedCommandScreen`,
    the same as the screen's own `+` binding does. Deliberately *not* focusable
    (`can_focus` stays `False`, `Static`'s own default): a focusable widget is auto-focused the
    moment a screen mounts if nothing else claims focus first (Textual's default `Screen.
    AUTO_FOCUS` behavior), which — before this was caught — silently stole every subsequent
    Enter keystroke via a widget-level binding, permanently breaking `PermissionAskScreen`'s own
    Enter-to-confirm the moment any command was long enough to truncate. `+` is this feature's
    only keyboard path for exactly that reason; a click is the only other one.
    """

    def __init__(self, on_activate: Callable[[], None], *, classes: str | None = None) -> None:
        super().__init__("[more...]", id=PERMISSION_ASK_MORE_ID, classes=classes)
        self._on_activate = on_activate

    def on_click(self) -> None:
        self._on_activate()


class ExpandedCommandScreen(ModalScreen[None]):
    """Full-screen, read-only, scrollable view of a permission ask's complete `command_text` —
    reached from `PermissionAskScreen` via its `+` binding or the `[more...]` indicator
    (`_MoreIndicator`) a long, truncated command preview shows. Dismissed with Escape or Enter,
    returning to the ask modal underneath with its own state (grid cursor position, etc.)
    untouched — pushing/popping a screen, rather than mutating `PermissionAskScreen` in place,
    keeps that state automatically preserved for free."""

    CSS = """
    ExpandedCommandScreen {
        align: center middle;
    }

    ExpandedCommandScreen VerticalScroll {
        width: 90%;
        height: 90%;
        border: round $accent;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("enter", "close", "Close"),
    ]

    def __init__(self, command_text: str) -> None:
        super().__init__()
        self._command_text = command_text

    def compose(self) -> ComposeResult:
        yield VerticalScroll(Static(self._command_text))

    def action_close(self) -> None:
        self.dismiss(None)


class PermissionAskScreen(ModalScreen[PermissionDecision]):
    """Presents a 2D grid of `_ACTIONS` (columns: Allow, Deny) by `_SCOPES` (rows: once,
    session, workspace, homedir) that the user navigates with arrow keys — Left/Right cycles
    the Allow/Deny column, Up/Down cycles the row — and confirms with Enter, dismissing
    `PermissionDecision(action=_ACTIONS[column], scope=_SCOPES[row])`. Escape is a fast path to
    an outright `PermissionDecision(action="deny", scope="once")` without needing to navigate
    there first.

    Above the grid: a styled header naming the kind of access being requested ("Run command" if
    `ask_ctx.command_text` is set, else "Read file"/"Write file" if `ask_ctx.path` is set — see
    `_header_text`), then the command text or path itself (long commands truncated to
    `_MAX_COMMAND_PREVIEW_LINES` with a `[more...]` indicator — see `_command_preview`,
    `ExpandedCommandScreen`), then `ask_ctx.resource_description`'s own per-item detail (the
    specific argv/path/reason this one ask is about, which can differ from the full command text
    for a compound command needing several independent decisions). `[more...]` is also shown for
    a command short enough to display without truncation, whenever `ask_ctx.is_compound` is set:
    `resource_description` only ever names the one simple command/redirect/reason this particular
    item is about, so a compound command (`foo && bar`) always gets an explicit path to the full
    command line, even though it's already visible in the preview above — the user shouldn't have
    to notice on their own that a fully-visible short preview is still only one piece of what will
    actually run.

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

    #permission-ask-header {
        color: $text-warning;
        text-style: bold;
        margin: 0 0 1 0;
    }

    #permission-ask-command {
        text-style: bold;
    }

    #permission-ask-more {
        color: $accent;
        text-style: underline;
    }

    .ask-section-end {
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
        Binding("plus", "expand_command", "Expand", show=False),
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

        widgets: list[Widget] = [Static(self._header_text(), id=PERMISSION_ASK_HEADER_ID)]

        command_text = self._ask_ctx.command_text
        if command_text is not None:
            preview, truncated = _command_preview(command_text)
            show_more = truncated or self._ask_ctx.is_compound
            if show_more:
                widgets.append(Static(preview, id=PERMISSION_ASK_COMMAND_ID))
                widgets.append(_MoreIndicator(
                    on_activate=self.action_expand_command, classes=_SECTION_END_CLASS))
            else:
                widgets.append(Static(
                    preview, id=PERMISSION_ASK_COMMAND_ID, classes=_SECTION_END_CLASS))
        elif self._ask_ctx.path is not None:
            widgets.append(Static(
                str(self._ask_ctx.path), id=PERMISSION_ASK_COMMAND_ID, classes=_SECTION_END_CLASS))

        widgets.append(Static(
            self._ask_ctx.resource_description, id=PERMISSION_ASK_DETAIL_ID,
            classes=_SECTION_END_CLASS))

        granted_text = self._granted_text()
        if granted_text is not None:
            widgets.append(Static(
                granted_text, id=PERMISSION_ASK_GRANTED_ID, classes=_SECTION_END_CLASS))

        widgets.append(Grid(*cells, id=PERMISSION_ASK_GRID_ID))
        widgets.append(Static(
            "←/→ Allow/Deny   ↑/↓ scope   Enter confirm   O other   Esc deny",
            id="permission-ask-hint"))

        yield Vertical(*widgets, id="permission-ask-body")

    def on_mount(self) -> None:
        self._refresh_selection()

    def _header_text(self) -> str:
        if self._ask_ctx.command_text is not None:
            kind = "Run command"
        elif self._ask_ctx.path is not None:
            kind = "Write file" if self._ask_ctx.is_write else "Read file"
        else:
            kind = "Confirm"
        return f"Permission requested: {kind}"

    def _granted_text(self) -> str | None:
        if self._granted_paths:
            directories = ", ".join(str(path) for path in self._granted_paths)
            return (
                "Any persistent Allow choice below grants access to the whole directory:\n"
                f"{directories}")
        if self._granted_command_patterns:
            patterns = ", ".join(" ".join(pattern) for pattern in self._granted_command_patterns)
            return f"Any persistent Allow choice below grants: {patterns}"
        return None

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

    def action_expand_command(self) -> None:
        if self._ask_ctx.command_text is not None:
            self.app.push_screen(ExpandedCommandScreen(self._ask_ctx.command_text))

    def _reveal_other_input(self) -> None:
        self.query_one(f"#{PERMISSION_ASK_GRID_ID}", Grid).remove()
        self.query_one("#permission-ask-hint", Static).remove()
        other_input = Input(
            placeholder="Explain why, or what to allow instead...", id=PERMISSION_ASK_INPUT_ID)
        self.query_one("#permission-ask-body", Vertical).mount(other_input)
        other_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(PermissionDecision(action="deny", scope="once", other_text=event.value))
