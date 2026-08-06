# © Copyright 2026 Aaron Kimball
"""EvalCase tasks covering CreateSubagent/WaitForSubagent, grouped into the "subagents"
EvalSuite -- doesn't belong under `cases.py`'s "file-tools" suite (see that module's own
docstring), since it exercises the operator role launching and waiting on a specialist
subagent session rather than any one file tool directly.

`_check_create_subagent_and_wait_for_answer` grades on both the resulting workspace file state
and `session.messages` (via `klorb.evals.harness.tool_call_args`) -- filesystem state alone
can't tell a genuine CreateSubagent/WaitForSubagent round trip apart from the operator simply
reading the marker file itself and skipping delegation entirely, since both tool sets are
available to it (see docs/adrs/grade-tool-evals-by-filesystem-state.md's "where useful"
carve-out).
"""

from pathlib import Path

from klorb.permissions.skill_access import SkillRules
from klorb.session import Session

from .harness import EvalCase, EvalSuite, tool_call_args

_MARKER_CODE = "qxr-77234-zeta"

_MARKER_FILE_CONTENT = (
    "Field notes, unrelated preamble.\n"
    f"MARKER: {_MARKER_CODE}\n"
    "More unrelated trailing text below this line.\n"
)


def _check_create_subagent_and_wait_for_answer(workspace_root: Path, session: Session) -> str | None:
    answer_path = workspace_root / "answer.txt"
    if not answer_path.exists():
        return "answer.txt was not created"
    content = answer_path.read_text(encoding="utf-8").strip()
    if content != _MARKER_CODE:
        return f"answer.txt contains {content!r}, expected {_MARKER_CODE!r}"

    create_calls = tool_call_args(session, "CreateSubagent")
    if not create_calls:
        return "CreateSubagent was never called"
    if create_calls[0].get("role") != "explorer":
        return f"CreateSubagent's role arg was {create_calls[0].get('role')!r}, expected 'explorer'"
    if not tool_call_args(session, "WaitForSubagent"):
        return "WaitForSubagent was never called"
    return None


CREATE_SUBAGENT_AND_WAIT_FOR_ANSWER = EvalCase(
    name="create_subagent_and_wait_for_answer",
    prompt=(
        "Somewhere under this workspace, a file contains a line starting with 'MARKER: ' "
        "followed by a short code. Use CreateSubagent to launch a subagent with role="
        "\"explorer\", instructing it to search the workspace and find that marker code and "
        "report it back. Once you've started the subagent, use WaitForSubagent to receive its "
        "answer. Then write ONLY the marker code (nothing else) to a new file named answer.txt "
        "in the workspace root."
    ),
    setup_files={"notes/log.txt": _MARKER_FILE_CONTENT},
    # The operator's system prompt points it at this skill for delegating research to an
    # explorer subagent; pre-allow it since a headless eval has no ask surface to approve
    # its first activation.
    skill_rules=SkillRules(allow=[("internal", "launch-explorer-subagent")]),
    check=_check_create_subagent_and_wait_for_answer,
    expected_tool_calls=4,  # ActivateSkill, CreateSubagent, WaitForSubagent, CreateFile
)

SUBAGENT_CASES: list[EvalCase] = [
    CREATE_SUBAGENT_AND_WAIT_FOR_ANSWER,
]

SUBAGENTS = EvalSuite(name="subagents", cases=SUBAGENT_CASES)
