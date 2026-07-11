# © Copyright 2026 Aaron Kimball
"""Modal shown for each question of an `AskUserQuestionsTool` call (see
`klorb.session.AskUserQuestionsItemContext`/`AskUserQuestionsAnswer` and
docs/specs/ask-user-questions.md)."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Static

from klorb.session import AskUserQuestionsAnswer, AskUserQuestionsItemContext
from klorb.tools.ask.common import format_answer

ASK_USER_QUESTIONS_HEADER_ID = "ask-user-questions-header"
ASK_USER_QUESTIONS_TEXT_ID = "ask-user-questions-text"
ASK_USER_QUESTIONS_LIST_ID = "ask-user-questions-list"
ASK_USER_QUESTIONS_INPUT_ID = "ask-user-questions-other-input"
ASK_USER_QUESTIONS_OTHER_ROW_ID = "ask-user-questions-row-other"

_SECTION_END_CLASS = "ask-section-end"


def _row_id(row: int) -> str:
    return f"ask-user-questions-row-{row}"


class AskUserQuestionsScreen(ModalScreen[AskUserQuestionsAnswer]):
    """Presents one question from an `AskUserQuestionsItemContext`: a single-column,
    Up/Down-navigable list of its `options` (the first one bold-badged `"(Recommended)"` when
    `QuestionOption.recommended` is set — recommendation is purely this display hint, never an
    auto-selected default) plus a trailing, always-present "Other..." row. Confirming an option
    row with Enter dismisses with `AskUserQuestionsAnswer(answer=format_answer(option, None))`;
    confirming "Other..." (or pressing `o`, a fast path that works from any row) reveals a
    free-text `Input` whose submission dismisses with
    `AskUserQuestionsAnswer(answer=format_answer(None, input.value))`. Escape dismisses with
    `AskUserQuestionsAnswer(cancelled=True)` from any state, including from inside the revealed
    `Input` — there is no deny/allow axis to fall back to the way `PermissionAskScreen` has, so
    Escape here means "stop asking me this" outright.

    A question with zero `options` (a plain free-text question) skips the list entirely and
    reveals the `Input` immediately on mount, since there is nothing else to navigate to.
    """

    CSS = """
    AskUserQuestionsScreen {
        align: center middle;
    }

    AskUserQuestionsScreen Vertical {
        width: auto;
        max-width: 84;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }

    #ask-user-questions-header {
        color: $text-warning;
        text-style: bold;
        margin: 0 0 1 0;
    }

    #ask-user-questions-text {
        text-style: bold;
    }

    .ask-section-end {
        margin: 0 0 1 0;
    }

    #ask-user-questions-list {
        width: 72;
        height: auto;
        margin: 0 0 1 0;
    }

    #ask-user-questions-list Static {
        width: 100%;
        height: auto;
        padding: 0 1;
    }

    #ask-user-questions-list Static.selected {
        background: $accent;
        color: $text;
        text-style: bold;
    }

    #ask-user-questions-hint {
        color: $text-muted;
    }

    #ask-user-questions-other-input {
        width: 72;
    }
    """

    BINDINGS = [
        Binding("up", "move_row(-1)", "Up", show=False),
        Binding("down", "move_row(1)", "Down", show=False),
        Binding("enter", "confirm", "Confirm"),
        Binding("escape", "cancel", "Cancel"),
        Binding("o", "other", "Other..."),
    ]

    def __init__(self, ask_ctx: AskUserQuestionsItemContext) -> None:
        super().__init__()
        self._ask_ctx = ask_ctx
        self._row = 0
        self._other_row = len(ask_ctx.options)
        self._total_rows = self._other_row + 1

    def compose(self) -> ComposeResult:
        widgets: list[Widget] = [Static(self._header_text(), id=ASK_USER_QUESTIONS_HEADER_ID)]
        widgets.append(Static(
            self._ask_ctx.question, id=ASK_USER_QUESTIONS_TEXT_ID, classes=_SECTION_END_CLASS))

        if self._ask_ctx.options:
            rows: list[Static] = [
                Static(self._option_row_text(index), id=_row_id(index))
                for index in range(len(self._ask_ctx.options))
            ]
            rows.append(Static("Other...", id=ASK_USER_QUESTIONS_OTHER_ROW_ID))
            widgets.append(Vertical(*rows, id=ASK_USER_QUESTIONS_LIST_ID))
            widgets.append(Static(
                "↑/↓ select   Enter confirm   O other   Esc cancel",
                id="ask-user-questions-hint"))

        yield Vertical(*widgets, id="ask-user-questions-body")

    def on_mount(self) -> None:
        if self._ask_ctx.options:
            self._refresh_selection()
        else:
            self._reveal_other_input()

    def _header_text(self) -> str:
        return f"Question {self._ask_ctx.index + 1} of {self._ask_ctx.total} · {self._ask_ctx.header}"

    def _option_row_text(self, index: int) -> str:
        option = self._ask_ctx.options[index]
        prefix = "(Recommended) " if option.recommended else ""
        label = f"[bold]{prefix}{option.label}:[/bold]"
        return f"{label} {option.description}" if option.description else label

    def _row_widget_id(self, row: int) -> str:
        return ASK_USER_QUESTIONS_OTHER_ROW_ID if row == self._other_row else _row_id(row)

    def _refresh_selection(self) -> None:
        list_container = self.query_one(f"#{ASK_USER_QUESTIONS_LIST_ID}", Vertical)
        for row in range(self._total_rows):
            widget = list_container.query_one(f"#{self._row_widget_id(row)}", Static)
            widget.set_class(row == self._row, "selected")

    def action_move_row(self, delta: int) -> None:
        self._row = (self._row + delta) % self._total_rows
        self._refresh_selection()

    def action_confirm(self) -> None:
        if self._row == self._other_row:
            self._reveal_other_input()
            return
        option = self._ask_ctx.options[self._row]
        self.dismiss(AskUserQuestionsAnswer(answer=format_answer(option, None)))

    def action_cancel(self) -> None:
        self.dismiss(AskUserQuestionsAnswer(cancelled=True))

    def action_other(self) -> None:
        self._reveal_other_input()

    def _reveal_other_input(self) -> None:
        body = self.query_one("#ask-user-questions-body", Vertical)
        if self.query(f"#{ASK_USER_QUESTIONS_LIST_ID}"):
            self.query_one(f"#{ASK_USER_QUESTIONS_LIST_ID}", Vertical).remove()
        if self.query("#ask-user-questions-hint"):
            self.query_one("#ask-user-questions-hint", Static).remove()
        other_input = Input(placeholder="Type your answer...", id=ASK_USER_QUESTIONS_INPUT_ID)
        body.mount(other_input)
        other_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(AskUserQuestionsAnswer(answer=format_answer(None, event.value)))
