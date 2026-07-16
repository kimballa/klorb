# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.panels.ask_user_questions_panel.AskUserQuestionsPanel."""

from unittest.mock import MagicMock

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Static

from klorb.session import AskUserQuestionsAnswer, AskUserQuestionsItemContext
from klorb.tools.ask.common import QuestionOption
from klorb.tui.panels.ask_user_questions_panel import (
    ASK_USER_QUESTIONS_HEADER_ID,
    ASK_USER_QUESTIONS_INPUT_ID,
    ASK_USER_QUESTIONS_LIST_ID,
    ASK_USER_QUESTIONS_OTHER_ROW_ID,
    AskUserQuestionsPanel,
)


def _ctx(
    *, header: str = "Auth", options: list[QuestionOption] | None = None,
    index: int = 0, total: int = 1,
) -> AskUserQuestionsItemContext:
    return AskUserQuestionsItemContext(
        header=header, question="Which auth method?",
        options=[] if options is None else options, index=index, total=total)


def _option(label: str, description: str | None = None, recommended: bool = False) -> QuestionOption:
    return QuestionOption(label=label, description=description, recommended=recommended)


def _find_child(container: object, widget_id: str) -> object:
    return next(
        widget for widget in container._pending_children  # type: ignore[attr-defined]
        if widget.id == widget_id)


# --- unit-level (compose() without mounting) ---


def test_header_names_question_index_and_header() -> None:
    panel = AskUserQuestionsPanel(_ctx(header="Auth", index=1, total=3))

    container = next(iter(panel.compose()))
    header = _find_child(container, ASK_USER_QUESTIONS_HEADER_ID)

    assert isinstance(header, Static)
    assert str(header.render()) == "Question 2 of 3 · Auth"


def test_option_rows_plus_a_trailing_other_row() -> None:
    panel = AskUserQuestionsPanel(_ctx(options=[_option("JWT"), _option("Cookie")]))

    container = next(iter(panel.compose()))
    option_list = _find_child(container, ASK_USER_QUESTIONS_LIST_ID)

    labels = [child.id for child in option_list._pending_children]  # type: ignore[attr-defined]
    assert labels == ["ask-user-questions-row-0", "ask-user-questions-row-1", ASK_USER_QUESTIONS_OTHER_ROW_ID]


def test_recommended_option_is_badged_in_its_row_text() -> None:
    panel = AskUserQuestionsPanel(_ctx(options=[
        _option("JWT", description="Stateless", recommended=True), _option("Cookie"),
    ]))

    container = next(iter(panel.compose()))
    option_list = _find_child(container, ASK_USER_QUESTIONS_LIST_ID)
    first_row = _find_child(option_list, "ask-user-questions-row-0")

    assert isinstance(first_row, Static)
    assert str(first_row.render()) == "(Recommended) JWT: Stateless"


def test_option_with_no_description_renders_just_the_label() -> None:
    panel = AskUserQuestionsPanel(_ctx(options=[_option("JWT"), _option("Cookie")]))

    container = next(iter(panel.compose()))
    option_list = _find_child(container, ASK_USER_QUESTIONS_LIST_ID)
    second_row = _find_child(option_list, "ask-user-questions-row-1")

    assert isinstance(second_row, Static)
    assert str(second_row.render()) == "Cookie:"


def test_zero_options_skips_the_list_entirely() -> None:
    panel = AskUserQuestionsPanel(_ctx(options=[]))

    container = next(iter(panel.compose()))

    assert not any(
        getattr(child, "id", None) == ASK_USER_QUESTIONS_LIST_ID
        for child in container._pending_children  # type: ignore[attr-defined]
    )


# --- action_* unit tests (mirroring PermissionAskPanel's test style) ---


def test_action_confirm_dismisses_with_the_selected_options_answer() -> None:
    panel = AskUserQuestionsPanel(_ctx(options=[
        _option("JWT", description="Stateless"), _option("Cookie"),
    ]))
    panel.dismiss = MagicMock()  # type: ignore[method-assign]
    panel._row = 0

    panel.action_confirm()

    panel.dismiss.assert_called_once_with(AskUserQuestionsAnswer(answer="JWT: Stateless"))


def test_action_confirm_on_other_row_reveals_input_instead_of_dismissing() -> None:
    panel = AskUserQuestionsPanel(_ctx(options=[_option("JWT"), _option("Cookie")]))
    panel.dismiss = MagicMock()  # type: ignore[method-assign]
    panel._reveal_other_input = MagicMock()  # type: ignore[method-assign]
    panel._row = panel._other_row

    panel.action_confirm()

    panel._reveal_other_input.assert_called_once()
    panel.dismiss.assert_not_called()


def test_action_cancel_dismisses_with_cancelled() -> None:
    panel = AskUserQuestionsPanel(_ctx(options=[_option("JWT"), _option("Cookie")]))
    panel.dismiss = MagicMock()  # type: ignore[method-assign]

    panel.action_cancel()

    panel.dismiss.assert_called_once_with(AskUserQuestionsAnswer(cancelled=True))


def test_action_move_row_wraps_around_past_the_other_row() -> None:
    panel = AskUserQuestionsPanel(_ctx(options=[_option("JWT"), _option("Cookie")]))
    panel._refresh_selection = MagicMock()  # type: ignore[method-assign]

    panel.action_move_row(-1)

    assert panel._row == 2  # wrapped from row 0 past the trailing "Other" row (index 2)


def test_on_input_submitted_dismisses_with_the_typed_text() -> None:
    panel = AskUserQuestionsPanel(_ctx(options=[]))
    panel.dismiss = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(value="my free-text answer")

    panel.on_input_submitted(event)

    panel.dismiss.assert_called_once_with(AskUserQuestionsAnswer(answer="my free-text answer"))


def test_dismiss_with_no_callback_is_a_no_op() -> None:
    """`dismiss()` is only ever meaningfully wired up by `ReplApp` -- a standalone panel (as
    every test above constructs) has no `on_dismiss` callback and must not raise."""
    panel = AskUserQuestionsPanel(_ctx(options=[_option("JWT"), _option("Cookie")]))

    panel.dismiss(AskUserQuestionsAnswer(cancelled=True))


# --- mounted, through a real App (needs Pilot for key handling) ---


class _AskUserQuestionsTestApp(App[None]):
    """Minimal standalone harness mounting a real `AskUserQuestionsPanel` directly onto the
    screen (rather than through `ReplApp`'s `#interaction-panel`) so tests can drive its key
    bindings through Textual's `Pilot` without needing a full `ReplApp`/`Session`."""

    def __init__(self, ctx: AskUserQuestionsItemContext) -> None:
        super().__init__()
        self._ctx = ctx
        self.answer: AskUserQuestionsAnswer | None = None

    def compose(self) -> ComposeResult:
        yield Vertical()

    async def on_mount(self) -> None:
        panel = AskUserQuestionsPanel(self._ctx, on_dismiss=self._set_answer)
        await self.query_one(Vertical).mount(panel)

    def _set_answer(self, answer: AskUserQuestionsAnswer) -> None:
        self.answer = answer


async def test_zero_options_question_reveals_input_on_mount() -> None:
    app = _AskUserQuestionsTestApp(_ctx(options=[]))

    async with app.run_test() as pilot:
        await pilot.pause()

        app.query_one(AskUserQuestionsPanel)
        app.query_one(f"#{ASK_USER_QUESTIONS_INPUT_ID}", Input)


async def test_escape_cancels_from_the_option_list() -> None:
    app = _AskUserQuestionsTestApp(_ctx(options=[_option("JWT"), _option("Cookie")]))

    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()

        assert app.answer == AskUserQuestionsAnswer(cancelled=True)


async def test_enter_on_first_row_selects_the_first_option() -> None:
    app = _AskUserQuestionsTestApp(_ctx(options=[_option("JWT"), _option("Cookie")]))

    async with app.run_test() as pilot:
        await pilot.press("enter")
        await pilot.pause()

        assert app.answer == AskUserQuestionsAnswer(answer="JWT")


async def test_o_key_reveals_other_input_and_submitting_answers_with_free_text() -> None:
    app = _AskUserQuestionsTestApp(_ctx(options=[_option("JWT"), _option("Cookie")]))

    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()

        input_widget = app.query_one(f"#{ASK_USER_QUESTIONS_INPUT_ID}", Input)
        input_widget.value = "custom answer"
        await pilot.press("enter")
        await pilot.pause()

        assert app.answer == AskUserQuestionsAnswer(answer="custom answer")


async def test_escape_from_inside_the_other_input_still_cancels() -> None:
    app = _AskUserQuestionsTestApp(_ctx(options=[_option("JWT"), _option("Cookie")]))

    async with app.run_test() as pilot:
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app.answer == AskUserQuestionsAnswer(cancelled=True)


async def test_bracketed_option_and_question_text_do_not_crash_at_reflow() -> None:
    """Model-authored labels, descriptions, headers, and question text can contain a literal
    `[` that Rich would otherwise parse as console markup and crash the compositor with a
    `MarkupError` at reflow (not at `Static.render()`, so this must mount through a real app to
    reproduce). See docs/adrs/style-arbitrary-text-spans-with-content-not-escaped-markup.md."""
    app = _AskUserQuestionsTestApp(AskUserQuestionsItemContext(
        header="Pick [an-option]", question="Which [foo-bar] do you want?",
        options=[_option("Use [foo-bar]", description="the [baz-qux] variant"), _option("Cookie")],
        index=0, total=1))

    async with app.run_test() as pilot:
        await pilot.pause()

        # Reaching here without a MarkupError is the assertion; confirm the panel actually
        # mounted and its bracketed first row rendered verbatim.
        app.query_one(AskUserQuestionsPanel)
        first_row = app.query_one("#ask-user-questions-row-0", Static)
        assert str(first_row.render()) == "Use [foo-bar]: the [baz-qux] variant"
