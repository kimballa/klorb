# © Copyright 2026 Aaron Kimball
"""Data types, parsing, and the `AskUserQuestionsRequired` exception shared between
`klorb.tools.ask.ask_user_questions.AskUserQuestionsTool` and `klorb.session` — kept in a leaf
module with no `klorb.tools.tool`/`klorb.tools.setup_context` import so `klorb.session` can
import it at module level (as it already does `klorb.permissions.table`'s exceptions)
without a cycle: `klorb.tools.tool` imports `klorb.tools.setup_context`, which imports
`klorb.session`, so a module `klorb.session` imports must never itself import `klorb.tools.tool`.
"""

from typing import Any

MAX_MULTI_CHOICE_OPTIONS = 5
"""The most `options` a single question may offer. More than this is rejected back to the
model as a validation error, with guidance to reframe the question with a narrower option
set rather than a long list the user has to scan."""


class QuestionOption:
    """One multiple-choice option offered for a `QuestionSpec`. `recommended`, if set, marks
    the model's suggested answer — display order and any "(Recommended)" badge is a TUI
    concern (see `klorb.tui.panels.ask_user_questions_panel.AskUserQuestionsPanel`); this class
    only carries the data.
    """

    def __init__(self, label: str, description: str | None, recommended: bool) -> None:
        self.label = label
        self.description = description
        self.recommended = recommended


class QuestionSpec:
    """One question out of an `AskUserQuestionsRequired` batch: `header` is a short chip-style
    label (roughly 12 characters or fewer), `question` is the full question text, and
    `options` is either empty (the user is shown a free-text box with no listed choices) or
    2-`MAX_MULTI_CHOICE_OPTIONS` `QuestionOption`s. An "Other: ___" free-text choice is always
    available in addition to any listed `options` — it is never itself one of them.
    """

    def __init__(self, header: str, question: str, options: list[QuestionOption]) -> None:
        self.header = header
        self.question = question
        self.options = options


class AskUserQuestionsRequired(Exception):
    """Raised by `AskUserQuestionsTool.apply()` for every well-formed `questions` argument —
    asking *is* this tool's entire job, so there is no other outcome to compute. Carries the
    parsed `QuestionSpec`s in the same order the model supplied them, for
    `Session._run_tool_calls` to ask about one at a time via `on_ask_user_questions`.
    """

    def __init__(self, questions: list[QuestionSpec]) -> None:
        super().__init__(f"User input required for {len(questions)} question(s).")
        self.questions = questions


def _parse_option(raw_option: dict[str, Any], *, index: int) -> QuestionOption:
    label = raw_option.get("label")
    if not isinstance(label, str) or not label:
        raise ValueError(f"Option {index} is missing a non-empty 'label'.")
    description = raw_option.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError(f"Option {index} ('{label}')'s 'description' must be a string.")
    recommended = bool(raw_option.get("recommended", False))
    if recommended and index != 0:
        raise ValueError(
            f"Option {index} ('{label}') is marked recommended, but only the first option "
            "(index 0) may be. Put the recommended option first, or recommend none at all.")
    return QuestionOption(label=label, description=description, recommended=recommended)


def _parse_question(raw_question: dict[str, Any], *, index: int) -> QuestionSpec:
    header = raw_question.get("header")
    if not isinstance(header, str) or not header:
        raise ValueError(f"Question {index} is missing a non-empty 'header'.")
    question = raw_question.get("question")
    if not isinstance(question, str) or not question:
        raise ValueError(f"Question {index} ('{header}') is missing a non-empty 'question'.")
    raw_options = raw_question.get("options", [])
    if not isinstance(raw_options, list):
        raise ValueError(f"Question {index} ('{header}')'s 'options' must be a list.")
    if len(raw_options) == 1:
        raise ValueError(
            f"Question {index} ('{header}') has exactly one option. Offer at least 2 "
            "options, or 0 to ask as a free-text question with no listed choices.")
    if len(raw_options) > MAX_MULTI_CHOICE_OPTIONS:
        raise ValueError(
            f"Question {index} ('{header}') has {len(raw_options)} options, more than the "
            f"maximum of {MAX_MULTI_CHOICE_OPTIONS}. Narrow the option set and ask again.")
    options = [
        _parse_option(raw_option, index=option_index)
        for option_index, raw_option in enumerate(raw_options)
    ]
    return QuestionSpec(header=header, question=question, options=options)


def parse_questions(raw_questions: list[dict[str, Any]]) -> list[QuestionSpec]:
    """Validate and parse `AskUserQuestionsTool`'s raw `questions` argument into `QuestionSpec`s,
    in the same order, raising `ValueError` with a model-actionable message on any malformed
    entry (see `_parse_question`/`_parse_option`)."""
    return [
        _parse_question(raw_question, index=index)
        for index, raw_question in enumerate(raw_questions)
    ]


def format_answer(option: QuestionOption | None, other_text: str | None) -> str:
    """Render one question's final answer as a single string, per
    `AskUserQuestionsAnswer.answer`'s contract (see `klorb.session`): a selected `option`
    renders as `"label: description"` (just `"label"` if it has no `description`); free text
    (`option is None`) renders as the raw `other_text` unchanged."""
    if option is None:
        return other_text or ""
    if option.description:
        return f"{option.label}: {option.description}"
    return option.label
