# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.ask.ask_user_questions and klorb.tools.ask.common."""

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.ask.ask_user_questions import AskUserQuestionsTool
from klorb.tools.ask.common import AskUserQuestionsRequired
from klorb.tools.setup_context import ToolSetupContext


def _tool() -> AskUserQuestionsTool:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())
    return AskUserQuestionsTool(context)


def _question(**overrides: object) -> dict:
    question: dict[str, object] = {
        "header": "Auth",
        "question": "Which auth method?",
        "options": [
            {"label": "JWT", "description": "Stateless", "recommended": True},
            {"label": "Session cookie"},
        ],
    }
    question.update(overrides)
    return question


def test_apply_raises_ask_user_questions_required_for_well_formed_input() -> None:
    tool = _tool()

    with pytest.raises(AskUserQuestionsRequired) as excinfo:
        tool.apply({"questions": [_question()]})

    (spec,) = excinfo.value.questions
    assert spec.header == "Auth"
    assert spec.question == "Which auth method?"
    assert [(o.label, o.description, o.recommended) for o in spec.options] == [
        ("JWT", "Stateless", True),
        ("Session cookie", None, False),
    ]


def test_apply_preserves_order_across_multiple_questions() -> None:
    tool = _tool()

    with pytest.raises(AskUserQuestionsRequired) as excinfo:
        tool.apply({"questions": [
            _question(header="First"),
            _question(header="Second", options=[]),
        ]})

    headers = [q.header for q in excinfo.value.questions]
    assert headers == ["First", "Second"]
    assert excinfo.value.questions[1].options == []


def test_apply_allows_zero_options_as_a_free_text_question() -> None:
    tool = _tool()

    with pytest.raises(AskUserQuestionsRequired) as excinfo:
        tool.apply({"questions": [_question(options=[])]})

    assert excinfo.value.questions[0].options == []


def test_apply_rejects_exactly_one_option() -> None:
    tool = _tool()

    with pytest.raises(ValueError, match="exactly one option"):
        tool.apply({"questions": [_question(options=[{"label": "Only one"}])]})


def test_apply_rejects_more_than_max_options() -> None:
    tool = _tool()
    options = [{"label": f"Option {i}"} for i in range(6)]

    with pytest.raises(ValueError, match="maximum of 5"):
        tool.apply({"questions": [_question(options=options)]})


def test_apply_accepts_exactly_max_options() -> None:
    tool = _tool()
    options = [{"label": f"Option {i}"} for i in range(5)]

    with pytest.raises(AskUserQuestionsRequired) as excinfo:
        tool.apply({"questions": [_question(options=options)]})

    assert len(excinfo.value.questions[0].options) == 5


def test_apply_rejects_recommended_on_a_non_first_option() -> None:
    tool = _tool()
    options = [
        {"label": "First"},
        {"label": "Second", "recommended": True},
    ]

    with pytest.raises(ValueError, match="only the first option"):
        tool.apply({"questions": [_question(options=options)]})


def test_apply_allows_no_recommended_option_at_all() -> None:
    tool = _tool()
    options = [{"label": "First"}, {"label": "Second"}]

    with pytest.raises(AskUserQuestionsRequired) as excinfo:
        tool.apply({"questions": [_question(options=options)]})

    assert [o.recommended for o in excinfo.value.questions[0].options] == [False, False]


def test_apply_rejects_missing_header() -> None:
    tool = _tool()

    with pytest.raises(ValueError, match="header"):
        tool.apply({"questions": [_question(header="")]})


def test_apply_rejects_empty_questions_list() -> None:
    tool = _tool()

    with pytest.raises(ValueError, match="non-empty"):
        tool.apply({"questions": []})


def test_summary_lists_question_headers() -> None:
    tool = _tool()

    summary = tool.summary({"questions": [_question(header="Auth"), _question(header="DB")]})

    assert summary == "Ask user (Auth, DB)"


def test_summary_includes_error_when_present() -> None:
    tool = _tool()

    summary = tool.summary({"questions": [_question()]}, error="boom")

    assert "boom" in summary


def test_detail_view_renders_each_answer() -> None:
    tool = _tool()
    result = {"answers": [{"header": "Auth", "question": "Which?", "answer": "JWT: Stateless"}]}

    detail = tool.detail_view({"questions": [_question()]}, result=result)

    assert "Auth: Which?" in detail
    assert "JWT: Stateless" in detail
