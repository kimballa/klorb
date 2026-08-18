# © Copyright 2026 Aaron Kimball
"""Panel shown in the history scroll when a tool call hits an `"ask"` permission verdict."""

import textwrap
from typing import Callable, Literal

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Static

from klorb.permissions.resource import GrantPreview
from klorb.session import PermissionAskContext, PermissionDecision

_Action = Literal["allow", "deny"]
_Scope = Literal["once", "session", "workspace", "homedir"]

_ACTIONS: tuple[_Action, ...] = ("allow", "deny")
"""Grid columns, left to right."""
_SCOPES: tuple[_Scope, ...] = ("once", "session", "workspace", "homedir")
"""Grid rows, top to bottom."""

_ACTION_LABELS: dict[_Action, str] = {"allow": "Allow", "deny": "Deny"}
_SCOPE_LABELS: dict[_Scope, str] = {
    "once": "Once",
    "session": "This session",
    "workspace": "Always, this workspace",
    "homedir": "Always, for me",
}

PERMISSION_ASK_HEADER_ID = "permission-ask-header"
PERMISSION_ASK_RISK_BADGE_ID = "permission-ask-risk-badge"
PERMISSION_ASK_INTENT_ID = "permission-ask-intent"
PERMISSION_ASK_COMMAND_ID = "permission-ask-command"
PERMISSION_ASK_MORE_ID = "permission-ask-more"
PERMISSION_ASK_RATIONALE_ID = "permission-ask-rationale"
PERMISSION_ASK_DETAIL_ID = "permission-ask-detail"
PERMISSION_ASK_GRANTED_ID = "permission-ask-granted"
PERMISSION_ASK_GRID_ID = "permission-ask-grid"
PERMISSION_ASK_INPUT_ID = "permission-ask-other-input"
PERMISSION_ASK_OTHER_CELL_ID = "permission-ask-cell-other"

_OTHER_ROW = len(_SCOPES)
"""The grid's last row index, occupied by a single cell spanning both columns."""
_TOTAL_ROWS = len(_SCOPES) + 1
"""`_SCOPES`'s rows plus the trailing `_OTHER_ROW`."""

_MAX_COMMAND_PREVIEW_LINES = 6
"""How many lines of a long `command_text` `PermissionAskPanel` shows inline before truncating
to a `[more...]` indicator."""

_MAX_SECONDARY_TEXT_LINES = 4
"""Height cap, in terminal rows, applied to every variable-content body `Static` other than
the command preview — the "Intent:" line, the risk rationale, the per-item
`resource_description` detail, and the granted-scope line."""

_SECTION_END_CLASS = "ask-section-end"
"""CSS class carrying the trailing blank-line margin between body sections."""

_RISK_BAND_CLASSES: tuple[tuple[int, str], ...] = (
    (9, "ask-risk-critical"), (7, "ask-risk-high"), (5, "ask-risk-medium"))
"""`(minimum_score, css_class)` pairs, checked highest-first, for `_risk_band_class()`:
9-10 is `"ask-risk-critical"` (red), 7-8 is `"ask-risk-high"` (orange),
5-6 is `"ask-risk-medium"` (yellow), and 0-4 gets no class at all."""


def _risk_band_class(risk_score: int) -> str | None:
    for minimum, css_class in _RISK_BAND_CLASSES:
        if risk_score >= minimum:
            return css_class
    return None


def _cell_id(column: int, row: int) -> str:
    return f"permission-ask-cell-{column}-{row}"


def format_ask_context_body(ask_ctx: PermissionAskContext) -> str:
    """Render `ask_ctx`'s intent, command/path/skill/domain preview, and its own
    `resource_description` detail as a flat block of text for the history-scroll record."""
    lines: list[str] = []
    if ask_ctx.bash_context is not None:
        if ask_ctx.bash_context.intent:
            lines.append(f"Intent: {ask_ctx.bash_context.intent}")
        lines.append(ask_ctx.bash_context.item_command_text or ask_ctx.bash_context.command_text)
    else:
        preview = ask_ctx.resource.preview_text()
        if preview is not None:
            lines.append(preview)
    lines.append(ask_ctx.resource_description)
    return "\n".join(lines)


def format_permission_decision(decision: PermissionDecision) -> str:
    """Render `decision` for the history-scroll record `ReplApp` leaves behind once a
    `PermissionAskPanel` is dismissed."""
    if decision.other_text:
        return f"Other: {decision.other_text}"
    return f"{_ACTION_LABELS[decision.action]} — {_SCOPE_LABELS[decision.scope]}"


def _command_preview(command_text: str, *, wrap_width: int | None = None) -> tuple[str, bool]:
    """Return `(preview_text, truncated)`: as much of `command_text` as fits within
    `_MAX_COMMAND_PREVIEW_LINES`. With `wrap_width` given, each logical line is also
    budgeted by how many visual rows it would soft-wrap to."""
    lines = command_text.splitlines() or [""]
    if wrap_width is None or wrap_width <= 0:
        if len(lines) <= _MAX_COMMAND_PREVIEW_LINES:
            return command_text, False
        return "\n".join(lines[:_MAX_COMMAND_PREVIEW_LINES]), True

    kept: list[str] = []
    rows_used = 0
    for line in lines:
        wrapped_rows = textwrap.wrap(
            line, width=wrap_width, break_long_words=True, replace_whitespace=False) or [""]
        if rows_used + len(wrapped_rows) > _MAX_COMMAND_PREVIEW_LINES:
            remaining = _MAX_COMMAND_PREVIEW_LINES - rows_used
            if remaining > 0:
                kept.append(" ".join(wrapped_rows[:remaining]))
            return "\n".join(kept), True
        kept.append(line)
        rows_used += len(wrapped_rows)
    return command_text, False


class _MoreIndicator(Static):
    """The `[more...]` affordance shown below a truncated command preview: clicking it calls
    `on_activate` to push `ExpandedCommandScreen`."""

    def __init__(self, on_activate: Callable[[], None], *, classes: str | None = None) -> None:
        super().__init__("[more...]", id=PERMISSION_ASK_MORE_ID, classes=classes)
        self._on_activate = on_activate

    def on_click(self) -> None:
        self._on_activate()


class ExpandedCommandScreen(ModalScreen[None]):
    """Full-screen, read-only, scrollable view of a permission ask's complete `command_text`.
    Dismissed with Escape or Enter."""

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
        # `markup=False`: `command_text` is arbitrary and must render verbatim -- a literal `[`
        # in the command is not content markup.
        yield VerticalScroll(Static(self._command_text, markup=False))

    def action_close(self) -> None:
        self.dismiss(None)


class PermissionAskPanel(Vertical):
    """Presents a 2D grid of actions (Allow/Deny) by scopes (once/session/workspace/homedir)
    that the user navigates with arrow keys and confirms with Enter. Escape is a fast path to
    deny-once. A trailing "Other..." row reveals a free-text input for responses the grid
    can't express."""

    can_focus = True

    DEFAULT_CSS = """
    PermissionAskPanel {
        width: 1fr;
        height: auto;
        border-top: solid $accent;
        padding: 1 2;
    }

    #permission-ask-body {
        width: 1fr;
        height: auto;
    }

    #permission-ask-header {
        color: $text-warning;
        text-style: bold;
        margin: 0 0 1 0;
    }

    #permission-ask-risk-badge {
        color: $text-muted;
        text-style: bold;
        margin: 0 0 1 0;
    }

    #permission-ask-intent {
        color: $text-muted;
        text-style: italic;
        margin: 0 0 1 0;
        width: 1fr;
    }

    #permission-ask-command {
        text-style: bold;
        width: 1fr;
    }

    #permission-ask-more {
        color: $accent;
        text-style: underline;
    }

    #permission-ask-rationale {
        text-style: italic;
        width: 1fr;
    }

    .ask-risk-medium {
        color: $warning-lighten-2;
    }

    .ask-risk-high {
        color: $warning;
    }

    .ask-risk-critical {
        color: $error;
    }

    .ask-section-end {
        margin: 0 0 1 0;
    }

    #permission-ask-detail, #permission-ask-granted {
        width: 1fr;
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
        granted_preview: GrantPreview | None = None,
        grant_patterns: list[list[str]] | None = None,
        initial_action: _Action = "allow",
        initial_scope: _Scope = "once",
        risk_score: int | None = None,
        risk_rationale: str | None = None,
        preview_wrap_width: int | None = None,
        on_dismiss: Callable[[PermissionDecision], None] | None = None,
    ) -> None:
        super().__init__()
        self._ask_ctx = ask_ctx
        self._granted_preview = granted_preview
        self._grant_patterns = grant_patterns
        self._column = _ACTIONS.index(initial_action)
        self._row = _SCOPES.index(initial_scope)
        self._risk_score = risk_score
        self._risk_rationale = risk_rationale
        self._preview_wrap_width = preview_wrap_width
        self._on_dismiss = on_dismiss

    def compose(self) -> ComposeResult:
        cells: list[Static] = [
            Static(f"{_ACTION_LABELS[action]} — {_SCOPE_LABELS[scope]}", id=_cell_id(column, row))
            for row, scope in enumerate(_SCOPES)
            for column, action in enumerate(_ACTIONS)
        ]
        cells.append(Static("Other...", id=PERMISSION_ASK_OTHER_CELL_ID))

        widgets: list[Widget] = [Static(self.header_text(), id=PERMISSION_ASK_HEADER_ID)]
        if self._risk_score is not None:
            badge_classes = _risk_band_class(self._risk_score)
            widgets.append(Static(
                f"Risk: {self._risk_score}/10", id=PERMISSION_ASK_RISK_BADGE_ID,
                classes=badge_classes))
        bash_context = self._ask_ctx.bash_context
        if bash_context is not None and bash_context.intent:
            # `markup=False`: the agent's own free-text intent statement must render verbatim.
            widgets.append(Static(
                f"Intent: {bash_context.intent}", id=PERMISSION_ASK_INTENT_ID, markup=False))

        preview_section: list[Widget] = []
        show_more = False
        if bash_context is not None:
            preview_source = bash_context.item_command_text or bash_context.command_text
            preview, truncated = _command_preview(preview_source, wrap_width=self._preview_wrap_width)
            show_more = truncated or bash_context.is_compound
            # `markup=False`: `preview` is arbitrary command text and must render verbatim -- a
            # literal `[` in an argv (e.g. a Python list comprehension) is not content markup, and
            # would otherwise raise MarkupError when the compositor reflows this widget.
            preview_section.append(Static(preview, id=PERMISSION_ASK_COMMAND_ID, markup=False))
            if show_more:
                preview_section.append(_MoreIndicator(on_activate=self.action_expand_command))
        else:
            preview_text = self._ask_ctx.resource.preview_text()
            if preview_text is not None:
                # `markup=False`: a path/URL can contain `[` and must render verbatim.
                preview_section.append(Static(
                    preview_text, id=PERMISSION_ASK_COMMAND_ID, markup=False))
        if self._risk_rationale is not None:
            # `markup=False`: the rationale is model-generated free text and must render verbatim.
            preview_section.append(Static(
                self._risk_rationale, id=PERMISSION_ASK_RATIONALE_ID, markup=False,
                classes=_risk_band_class(self._risk_score) if self._risk_score is not None else None))
        if preview_section:
            preview_section[-1].add_class(_SECTION_END_CLASS)
        widgets.extend(preview_section)

        # `markup=False`: `resource_description` carries arbitrary argv/path detail (e.g. a
        # bash item's own command text) and must render verbatim, not be parsed as content markup.
        widgets.append(Static(
            self._ask_ctx.resource_description, id=PERMISSION_ASK_DETAIL_ID, markup=False,
            classes=_SECTION_END_CLASS))

        granted_text = self._granted_text()
        if granted_text is not None:
            widgets.append(Static(
                granted_text, id=PERMISSION_ASK_GRANTED_ID, classes=_SECTION_END_CLASS))

        widgets.append(Grid(*cells, id=PERMISSION_ASK_GRID_ID))
        widgets.append(Static(
            # Build hint text with "+" when more content is available
            "   ".join(
                ["←/→ Allow/Deny", "↑/↓ scope", "Enter confirm", "O other", "Esc deny"]
                + (["+ expand"] if show_more else [])
            ),
            id="permission-ask-hint"))

        yield Vertical(*widgets, id="permission-ask-body")

    def on_mount(self) -> None:
        self._cap_body_static_heights()
        self._refresh_selection()
        self.focus()

    def _cap_body_static_heights(self) -> None:
        """Clip every variable-content body `Static` to a bounded number of rows so no amount of
        model- or resource-derived text can push the decision grid off screen."""
        caps: tuple[tuple[str, int], ...] = (
            (PERMISSION_ASK_COMMAND_ID, _MAX_COMMAND_PREVIEW_LINES),
            (PERMISSION_ASK_INTENT_ID, _MAX_SECONDARY_TEXT_LINES),
            (PERMISSION_ASK_RATIONALE_ID, _MAX_SECONDARY_TEXT_LINES),
            (PERMISSION_ASK_DETAIL_ID, _MAX_SECONDARY_TEXT_LINES),
            (PERMISSION_ASK_GRANTED_ID, _MAX_SECONDARY_TEXT_LINES),
        )
        for widget_id, max_lines in caps:
            for widget in self.query(f"#{widget_id}"):
                widget.styles.max_height = max_lines
                widget.styles.overflow_y = "hidden"

    def header_text(self) -> str:
        kind = (
            "Run command" if self._ask_ctx.bash_context is not None
            else self._ask_ctx.resource.header_kind())
        return f"Permission requested: {kind}"

    def _granted_text(self) -> Content | None:
        """The `#permission-ask-granted` copy naming the real scope a persistent Allow records at.

        Built as a `Content` with the resource as an explicitly-styled span rather than as a
        markup string, so the arbitrary argv/path text is never parsed as content markup."""
        if self._granted_preview is None:
            return None
        prefix = (
            "Any persistent Allow choice below grants access to the whole directory:\n"
            if self._granted_preview.block else "Any persistent Allow choice below grants: ")
        return Content.assemble(prefix, (self._granted_preview.resource_text, "$text-accent bold"))

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
        self.dismiss(PermissionDecision(
            action=_ACTIONS[self._column], scope=_SCOPES[self._row],
            grant_patterns=self._grant_patterns))

    def action_decline(self) -> None:
        self.dismiss(PermissionDecision(action="deny", scope="once"))

    def action_other(self) -> None:
        self._reveal_other_input()

    def action_expand_command(self) -> None:
        if self._ask_ctx.bash_context is not None:
            self.app.push_screen(ExpandedCommandScreen(self._ask_ctx.bash_context.command_text))

    def _reveal_other_input(self) -> None:
        self.query_one(f"#{PERMISSION_ASK_GRID_ID}", Grid).remove()
        self.query_one("#permission-ask-hint", Static).remove()
        other_input = Input(
            placeholder="Explain why, or what to allow instead...", id=PERMISSION_ASK_INPUT_ID)
        self.query_one("#permission-ask-body", Vertical).mount(other_input)
        other_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(PermissionDecision(action="deny", scope="once", other_text=event.value))

    def dismiss(self, decision: PermissionDecision) -> None:
        """Report `decision` to whoever mounted this panel, via the `on_dismiss` callback given
        at construction. A no-op with no callback given."""
        if self._on_dismiss is not None:
            self._on_dismiss(decision)
