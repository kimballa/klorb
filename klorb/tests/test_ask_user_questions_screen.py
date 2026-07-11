# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.ask_user_questions_screen.AskUserQuestionsScreen."""

from unittest.mock import MagicMock

from textual.app import App
from textual.widgets import Input, Static

from klorb.session import AskUserQuestionsAnswer, AskUserQuestionsItemContext
from klorb.tools.ask.common import QuestionOption
from klorb.tui.ask_user_questions_screen import (
    ASK_USER_QUESTIONS_HEADER_ID,
    ASK_USER_QUESTIONS_INPUT_ID,
    ASK_USER_QUESTIONS_LIST_ID,
    ASK_USER_QUESTIONS_OTHER_ROW_ID,
    AskUserQuestionsScreen,
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
    screen = AskUserQuestionsScreen(_ctx(header="Auth", index=1, total=3))

    container = next(iter(screen.compose()))
    header = _find_child(container, ASK_USER_QUESTIONS_HEADER_ID)

    assert isinstance(header, Static)
    assert str(header.render()) == "Question 2 of 3 · Auth"


def test_option_rows_plus_a_trailing_other_row() -> None:
    screen = AskUserQuestionsScreen(_ctx(options=[_option("JWT"), _option("Cookie")]))

    container = next(iter(screen.compose()))
    option_list = _find_child(container, ASK_USER_QUESTIONS_LIST_ID)

    labels = [child.id for child in option_list._pending_children]  # type: ignore[attr-defined]
    assert labels == ["ask-user-questions-row-0", "ask-user-questions-row-1", ASK_USER_QUESTIONS_OTHER_ROW_ID]


def test_recommended_option_is_badged_in_its_row_text() -> None:
    screen = AskUserQuestionsScreen(_ctx(options=[
        _option("JWT", description="Stateless", recommended=True), _option("Cookie"),
    ]))

    container = next(iter(screen.compose()))
    option_list = _find_child(container, ASK_USER_QUESTIONS_LIST_ID)
    first_row = _find_child(option_list, "ask-user-questions-row-0")

    assert isinstance(first_row, Static)
    assert str(first_row.render()) == "(Recommended) JWT: Stateless"


def test_option_with_no_description_renders_just_the_label() -> None:
    screen = AskUserQuestionsScreen(_ctx(options=[_option("JWT"), _option("Cookie")]))

    container = next(iter(screen.compose()))
    option_list = _find_child(container, ASK_USER_QUESTIONS_LIST_ID)
    second_row = _find_child(option_list, "ask-user-questions-row-1")

    assert isinstance(second_row, Static)
    assert str(second_row.render()) == "Cookie:"


def test_zero_options_skips_the_list_entirely() -> None:
    screen = AskUserQuestionsScreen(_ctx(options=[]))

    container = next(iter(screen.compose()))

    assert not any(
        getattr(child, "id", None) == ASK_USER_QUESTIONS_LIST_ID
        for child in container._pending_children  # type: ignore[attr-defined]
    )


# --- action_* unit tests (mirroring PermissionAskScreen's test style) ---


def test_action_confirm_dismisses_with_the_selected_options_answer() -> None:
    screen = AskUserQuestionsScreen(_ctx(options=[
        _option("JWT", description="Stateless"), _option("Cookie"),
    ]))
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    screen._row = 0

    screen.action_confirm()

    screen.dismiss.assert_called_once_with(AskUserQuestionsAnswer(answer="JWT: Stateless"))


def test_action_confirm_on_other_row_reveals_input_instead_of_dismissing() -> None:
    screen = AskUserQuestionsScreen(_ctx(options=[_option("JWT"), _option("Cookie")]))
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    screen._reveal_other_input = MagicMock()  # type: ignore[method-assign]
    screen._row = screen._other_row

    screen.action_confirm()

    screen._reveal_other_input.assert_called_once()
    screen.dismiss.assert_not_called()


def test_action_cancel_dismisses_with_cancelled() -> None:
    screen = AskUserQuestionsScreen(_ctx(options=[_option("JWT"), _option("Cookie")]))
    screen.dismiss = MagicMock()  # type: ignore[method-assign]

    screen.action_cancel()

    screen.dismiss.assert_called_once_with(AskUserQuestionsAnswer(cancelled=True))


def test_action_move_row_wraps_around_past_the_other_row() -> None:
    screen = AskUserQuestionsScreen(_ctx(options=[_option("JWT"), _option("Cookie")]))
    screen._refresh_selection = MagicMock()  # type: ignore[method-assign]

    screen.action_move_row(-1)

    assert screen._row == 2  # wrapped from row 0 past the trailing "Other" row (index 2)


def test_on_input_submitted_dismisses_with_the_typed_text() -> None:
    screen = AskUserQuestionsScreen(_ctx(options=[]))
    screen.dismiss = MagicMock()  # type: ignore[method-assign]
    event = MagicMock(value="my free-text answer")

    screen.on_input_submitted(event)

    screen.dismiss.assert_called_once_with(AskUserQuestionsAnswer(answer="my free-text answer"))


# --- mounted, through a real App (needs Pilot for key handling) ---


class _AskUserQuestionsTestApp(App[None]):
    def __init__(self, ctx: AskUserQuestionsItemContext) -> None:
        super().__init__()
        self._ctx = ctx
        self.answer: AskUserQuestionsAnswer | None = None

    def on_mount(self) -> None:
        self.push_screen(AskUserQuestionsScreen(self._ctx), callback=self._set_answer)

    def _set_answer(self, answer: AskUserQuestionsAnswer | None) -> None:
        self.answer = answer


async def test_zero_options_question_reveals_input_on_mount() -> None:
    app = _AskUserQuestionsTestApp(_ctx(options=[]))

    async with app.run_test() as pilot:
        await pilot.pause()

        assert isinstance(app.screen, AskUserQuestionsScreen)
        app.screen.query_one(f"#{ASK_USER_QUESTIONS_INPUT_ID}", Input)


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

        input_widget = app.screen.query_one(f"#{ASK_USER_QUESTIONS_INPUT_ID}", Input)
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
