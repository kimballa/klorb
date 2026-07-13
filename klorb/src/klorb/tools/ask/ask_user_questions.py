# © Copyright 2026 Aaron Kimball
"""A Tool that poses one or more structured questions to the user, instead of the model
guessing, fabricating an assumption, or re-deriving the same uncertain conclusion in a loop.

Mirrors the shape of the permission-ask mechanism (`klorb.permissions.table.
PermissionAskRequired`, `klorb.session.PermissionAskContext`/`PermissionDecision`): a `Tool`'s
`apply()` can't itself block on user input (see `Tool`'s "single `ToolSetupContext` argument"
contract), so `AskUserQuestionsTool.apply()` validates its arguments and, if they're
well-formed, always raises `AskUserQuestionsRequired` rather than returning a value.
`Session._run_tool_calls` catches it, asks the user one question at a time via
`TurnEventHandlers.on_ask_user_questions`, and assembles the answers into the tool's result.

The data types and validation logic themselves live in `klorb.tools.ask.common` rather than
here, so `klorb.session` can import them without importing this module (which pulls in
`klorb.tools.tool`/`klorb.tools.setup_context`, and so `klorb.session` itself — see that
module's docstring for the full cycle).
"""

from typing import Any

from klorb.tools.ask.common import AskUserQuestionsRequired, parse_questions
from klorb.tools.tool import Tool


class AskUserQuestionsTool(Tool):
    """Poses one or more multiple-choice (or, with no `options`, free-text) questions to the
    user in a single call, always raising `AskUserQuestionsRequired` for well-formed input
    (see module docstring for why). Use this instead of guessing at an ambiguous requirement,
    inventing a plausible default, or re-deriving the same uncertain conclusion more than
    once — see `default_sys.md`'s guidance on when to reach for this tool.
    """

    def name(self) -> str:
        return "AskUserQuestions"

    def description(self) -> str:
        return (
            "Ask the user one or more direct questions instead of guessing, assuming, or "
            "re-deriving the same uncertain conclusion in a loop. Use this when you notice "
            "you are agnostic among alternatives, guessing, second-guessing yourself, or "
            "trying several framings of the same question to yourself without resolving "
            "it — that is the signal to stop and ask, not to think harder. "
            "Each question may offer 2-5 short 'options' (each with a 'label' and optional "
            "'description'); at most one may be marked 'recommended', and it must be the "
            "first option. A free-text 'Other' choice is always available to the user in "
            "addition to any listed options, so don't add your own catch-all option. Pass "
            "an empty 'options' list to ask a plain free-text question with no listed "
            "choices. Ask several independent questions in one call rather than one at a "
            "time. 'header' is a short (roughly 12 character) label for the question."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "description": "One or more questions to ask the user in this call.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "header": {
                                "type": "string",
                                "description": "Short chip-style label, roughly <=12 characters.",
                            },
                            "question": {
                                "type": "string",
                                "description": "The full question text, ending in '?'.",
                            },
                            "options": {
                                "type": "array",
                                "description": (
                                    "0, or 2-5, choices. An implicit free-text 'Other' choice "
                                    "is always additionally offered to the user."
                                ),
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {
                                            "type": "string",
                                            "description": "The option's short display text.",
                                        },
                                        "description": {
                                            "type": "string",
                                            "description": "Optional clarifying detail.",
                                        },
                                        "recommended": {
                                            "type": "boolean",
                                            "description": (
                                                "Marks this as the recommended option. At "
                                                "most one per question, and only on the "
                                                "first option."
                                            ),
                                        },
                                    },
                                    "required": ["label"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["header", "question", "options"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["questions"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            raw_questions = args["questions"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'questions'. Provide a non-empty list of questions "
                "to ask the user.")
        if not isinstance(raw_questions, list) or not raw_questions:
            raise ValueError("'questions' must be a non-empty list.")
        raise AskUserQuestionsRequired(parse_questions(raw_questions))

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        headers = [q.get("header", "?") for q in args.get("questions", []) if isinstance(q, dict)]
        label = ", ".join(headers) if headers else "question"
        if error is not None:
            return f"Ask user ({label}): {error}"
        return f"Ask user ({label})"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"AskUserQuestions failed: {error}"
        if not isinstance(result, dict):
            return "AskUserQuestions: no answers recorded."
        lines = []
        for answer in result.get("answers", []):
            lines.append(f"{answer.get('header', '?')}: {answer.get('question', '?')}")
            lines.append(f"  -> {answer.get('answer', '')}")
        return "\n".join(lines) if lines else "AskUserQuestions: no answers recorded."
