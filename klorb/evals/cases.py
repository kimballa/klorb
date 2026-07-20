# © Copyright 2026 Aaron Kimball
"""Synthesized EvalCase tasks covering ReadFile, CreateFile, EditFile, ReplaceAll, ListDir, and
the skill tools (ActivateSkill/ReadSkillFile), grouped into the "file-tools" EvalSuite.

Each case's `check` inspects the resulting workspace file state only, never `final_response`
text — see docs/adrs/grade-tool-evals-by-filesystem-state.md. Content comparisons tolerate
incidental trailing-newline differences (a model may or may not end a file with a trailing
newline) but are otherwise exact: an eval case is meant to catch a model failing to use a tool
correctly, not to reward creative alternate phrasing of the outcome.

A handful of cases use files longer than `DEFAULT_READ_FILE_MAX_LINES` so a single `ReadFile`
call can't return the whole file, forcing a competent run to either page with
`start_line`/`end_line`, or locate the target line directly (e.g. via `Grep`) and skip paging
entirely — these also inspect `session.messages` for the `ReadFile`/`Grep`/`EditFile` calls
actually made (see docs/adrs/grade-tool-evals-by-filesystem-state.md's "where useful"
carve-out), not just the resulting file content.

Another handful use repeated, identical lines at or near a file's first/last line, where
`EditFile`'s drift search (see
docs/adrs/edit-file-tolerates-bounded-line-drift-via-local-candidate-search.md) necessarily
returns more than one candidate — these check that the model actually recovers by supplying
`context_before`/`context_after` (inspecting `session.messages` for those arguments, not just
the resulting content), rather than passing only because it stumbled onto the right file state
some other way.

A further handful cover the widened `EditFile` argument matrix (see
docs/specs/tool-framework.md): the single-line shortcut (`start_line == end_line`,
`end_text` omitted) and `old_text` (the whole replacement block verbatim). Since the file ends
up correct either way a case is written, these also inspect `session.messages` for the specific
argument shape the case exists to prove is reachable, not just the resulting content.
"""

import json
from pathlib import Path

from klorb.permissions.skill_access import SkillRules
from klorb.process_config import DEFAULT_READ_FILE_MAX_LINES
from klorb.session import Session

from .harness import EvalCase, EvalSuite


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _lines(path: Path) -> list[str]:
    return _read(path).splitlines()


def _tool_call_args(session: Session, tool_name: str) -> list[dict]:
    """Every call to `tool_name` the model made during `session`'s turn, as parsed argument
    dicts, in the order they were made."""
    return [
        json.loads(call.arguments)
        for message in session.messages
        if message.role == "tool_use" and message.tool_calls
        for call in message.tool_calls
        if call.name == tool_name
    ]


def _first_mismatch(expected: list[str], actual: list[str], filename: str) -> str | None:
    """Compare two line lists and describe the first difference, if any — used in place of an
    inline `!=` check for cases whose file is too long to usefully print in full on failure."""
    if len(expected) != len(actual):
        return f"{filename} should have {len(expected)} lines, got {len(actual)}"
    for line_num, (expected_line, actual_line) in enumerate(zip(expected, actual), start=1):
        if expected_line != actual_line:
            return f"{filename} line {line_num} should read {expected_line!r}, got {actual_line!r}"
    return None


def _check_create_file_basic(workspace_root: Path, session: Session) -> str | None:
    path = workspace_root / "greeting.txt"
    if not path.exists():
        return "greeting.txt was not created"
    if _read(path).strip() != "Hello, klorb!":
        return f"greeting.txt has unexpected content: {_read(path)!r}"
    return None


CREATE_FILE_BASIC = EvalCase(
    name="create_file_basic",
    prompt="Create a new file named greeting.txt containing exactly one line: Hello, klorb!",
    check=_check_create_file_basic,
    expected_tool_calls=1,
)


def _check_create_file_nested_directory(workspace_root: Path, session: Session) -> str | None:
    path = workspace_root / "notes" / "todo" / "today.txt"
    if not path.exists():
        return "notes/todo/today.txt was not created"
    if "buy milk" not in _read(path):
        return f"notes/todo/today.txt has unexpected content: {_read(path)!r}"
    return None


CREATE_FILE_NESTED_DIRECTORY = EvalCase(
    name="create_file_nested_directory",
    prompt=(
        "Create a file at notes/todo/today.txt containing exactly one line: buy milk. "
        "The notes/todo directory does not exist yet."
    ),
    check=_check_create_file_nested_directory,
    expected_tool_calls=1,
)


def _check_read_then_edit_single_line(workspace_root: Path, session: Session) -> str | None:
    lines = _lines(workspace_root / "todo.txt")
    if len(lines) != 3:
        return f"todo.txt should still have 3 lines, has {len(lines)}: {lines!r}"
    if lines[1] != "2. write tests":
        return f"line 2 should read '2. write tests', got {lines[1]!r}"
    if lines[0] != "1. wake up" or lines[2] != "3. sleep":
        return f"lines 1 and 3 should be unchanged, got {lines!r}"
    return None


READ_THEN_EDIT_SINGLE_LINE = EvalCase(
    name="read_then_edit_single_line",
    prompt=(
        "In todo.txt, change line 2 (currently about writing code) so it instead reads "
        "'2. write tests'. Leave every other line exactly as it is."
    ),
    setup_files={"todo.txt": "1. wake up\n2. write code\n3. sleep\n"},
    check=_check_read_then_edit_single_line,
    expected_tool_calls=3,
)


def _check_replace_all_literal(workspace_root: Path, session: Session) -> str | None:
    content = _read(workspace_root / "notes.txt")
    if "dog" in content:
        return f"notes.txt should no longer contain 'dog': {content!r}"
    if content.count("wolf") != 2:
        return f"notes.txt should contain 'wolf' twice, got: {content!r}"
    return None


REPLACE_ALL_LITERAL = EvalCase(
    name="replace_all_literal",
    prompt="In notes.txt, replace every occurrence of the word 'dog' with 'wolf'.",
    setup_files={"notes.txt": "The quick brown fox startled the dog. The dog ran away.\n"},
    check=_check_replace_all_literal,
    expected_tool_calls=2,
)


def _check_edit_file_insert_line(workspace_root: Path, session: Session) -> str | None:
    lines = _lines(workspace_root / "script.py")
    if not lines or "TODO" not in lines[0]:
        return f"first line should be the new TODO comment, got {lines[:1]!r}"
    if lines[1:] != ["import sys", "print(sys.argv)"]:
        return f"original two lines should be preserved unchanged after the insert, got {lines[1:]!r}"
    return None


EDIT_FILE_INSERT_LINE = EvalCase(
    name="edit_file_insert_line",
    prompt=(
        "Insert a new first line into script.py containing the comment "
        "'# TODO: add license header', without deleting or modifying any of its existing lines."
    ),
    setup_files={"script.py": "import sys\nprint(sys.argv)\n"},
    check=_check_edit_file_insert_line,
    expected_tool_calls=3,
)


def _check_avoid_overwrite_existing_file(workspace_root: Path, session: Session) -> str | None:
    content = _read(workspace_root / "config.txt")
    if "debug=true" not in content:
        return f"config.txt should now say debug=true, got: {content!r}"
    if "debug=false" in content:
        return f"config.txt should no longer say debug=false, got: {content!r}"
    return None


AVOID_OVERWRITE_EXISTING_FILE = EvalCase(
    name="avoid_overwrite_existing_file",
    prompt="Update config.txt so its debug setting is true instead of false.",
    setup_files={"config.txt": "debug=false\n"},
    check=_check_avoid_overwrite_existing_file,
    expected_tool_calls=2,
)


def _check_edit_file_middle_line_of_multiline_file(workspace_root: Path, session: Session) -> str | None:
    lines = _lines(workspace_root / "recipe.txt")
    expected = ["1 cup flour", "2 eggs", "1 cup sugar", "1 tsp vanilla"]
    if lines != expected:
        return f"recipe.txt should read {expected!r}, got {lines!r}"
    return None


EDIT_FILE_MIDDLE_LINE_OF_MULTILINE_FILE = EvalCase(
    name="edit_file_middle_line_of_multiline_file",
    prompt=(
        "In recipe.txt, change line 3 (currently '1 cup milk') to instead read "
        "'1 cup sugar'. Leave every other line exactly as it is."
    ),
    setup_files={"recipe.txt": "1 cup flour\n2 eggs\n1 cup milk\n1 tsp vanilla\n"},
    check=_check_edit_file_middle_line_of_multiline_file,
    expected_tool_calls=2,
)


def _check_edit_file_last_line_of_multiline_file(workspace_root: Path, session: Session) -> str | None:
    lines = _lines(workspace_root / "status.txt")
    expected = ["stage=build", "stage=test", "stage=deploy"]
    if lines != expected:
        return f"status.txt should read {expected!r}, got {lines!r}"
    return None


EDIT_FILE_LAST_LINE_OF_MULTILINE_FILE = EvalCase(
    name="edit_file_last_line_of_multiline_file",
    prompt=(
        "In status.txt, change the last line (currently 'stage=release') to instead read "
        "'stage=deploy'. Leave every other line exactly as it is."
    ),
    setup_files={"status.txt": "stage=build\nstage=test\nstage=release\n"},
    check=_check_edit_file_last_line_of_multiline_file,
    expected_tool_calls=3,
)


def _check_edit_file_line_content_starts_with_digit(workspace_root: Path, session: Session) -> str | None:
    lines = _lines(workspace_root / "inventory.txt")
    expected = ["3 apples", "7 oranges", "2 pears"]
    if lines != expected:
        return f"inventory.txt should read {expected!r}, got {lines!r}"
    return None


EDIT_FILE_LINE_CONTENT_STARTS_WITH_DIGIT = EvalCase(
    name="edit_file_line_content_starts_with_digit",
    prompt=(
        "In inventory.txt, change line 2 (currently '5 oranges') to instead read "
        "'7 oranges'. Leave every other line exactly as it is."
    ),
    setup_files={"inventory.txt": "3 apples\n5 oranges\n2 pears\n"},
    check=_check_edit_file_line_content_starts_with_digit,
    expected_tool_calls=2,
)


def _check_edit_file_two_edits_in_one_turn(workspace_root: Path, session: Session) -> str | None:
    lines = _lines(workspace_root / "chapters.txt")
    expected = ["Chapter One: Beginnings", "Chapter Two: Rising Action", "Chapter Three: Climax"]
    if lines != expected:
        return f"chapters.txt should read {expected!r}, got {lines!r}"
    return None


EDIT_FILE_TWO_EDITS_IN_ONE_TURN = EvalCase(
    name="edit_file_two_edits_in_one_turn",
    prompt=(
        "chapters.txt lists chapter titles. Change line 2 to read "
        "'Chapter Two: Rising Action' and line 3 to read 'Chapter Three: Climax'. "
        "Leave line 1 exactly as it is."
    ),
    setup_files={
        "chapters.txt": "Chapter One: Beginnings\nChapter Two: TODO\nChapter Three: TODO\n",
    },
    check=_check_edit_file_two_edits_in_one_turn,
    expected_tool_calls=3,
)


def _check_edit_file_copy_line_content_no_prefix_leak(workspace_root: Path, session: Session) -> str | None:
    path = workspace_root / "line3.txt"
    if not path.exists():
        return "line3.txt was not created"
    content = _read(path).strip()
    if content != "gamma":
        return f"line3.txt should contain exactly 'gamma' (no line-number prefix), got {content!r}"
    return None


EDIT_FILE_COPY_LINE_CONTENT_NO_PREFIX_LEAK = EvalCase(
    name="edit_file_copy_line_content_no_prefix_leak",
    prompt=(
        "Read source.txt and create a new file called line3.txt containing exactly the text "
        "of source.txt's line 3, and nothing else."
    ),
    setup_files={"source.txt": "alpha\nbeta\ngamma\ndelta\n"},
    check=_check_edit_file_copy_line_content_no_prefix_leak,
    expected_tool_calls=2,
)


_PAGINATION_FILE_TOTAL_LINES = DEFAULT_READ_FILE_MAX_LINES + 20
_PAGINATION_FILE_MARKER_LINE = DEFAULT_READ_FILE_MAX_LINES + 5


def _check_read_file_paginates_past_max_lines(workspace_root: Path, session: Session) -> str | None:
    expected = [f"row {i:03d}" for i in range(1, _PAGINATION_FILE_TOTAL_LINES + 1)]
    expected[_PAGINATION_FILE_MARKER_LINE - 1] = "SECRET_PASSWORD=changed123"
    mismatch = _first_mismatch(expected, _lines(workspace_root / "config.txt"), "config.txt")
    if mismatch is not None:
        return mismatch

    read_calls = _tool_call_args(session, "ReadFile")
    if len(read_calls) >= 2:
        return None

    # Alternate valid path: locate the marker line directly with Grep instead of paging
    # through ReadFile -- strictly fewer tool calls and just as reliable, so it passes too
    # rather than only rewarding the pagination path.
    if not read_calls and _tool_call_args(session, "Grep") and _tool_call_args(session, "EditFile"):
        return None

    return (
        f"expected either at least 2 ReadFile calls to page past the default "
        f"{DEFAULT_READ_FILE_MAX_LINES}-line max_lines cap and reach line "
        f"{_PAGINATION_FILE_MARKER_LINE}, or a Grep call to find the marker directly followed "
        f"by an EditFile call with no ReadFile paging at all, got {len(read_calls)} ReadFile "
        "call(s)"
    )


READ_FILE_PAGINATES_PAST_MAX_LINES = EvalCase(
    name="read_file_paginates_past_max_lines",
    prompt=(
        f"config.txt has {_PAGINATION_FILE_TOTAL_LINES} lines. Exactly one line contains the "
        "marker 'SECRET_PASSWORD=' — it is not necessarily near the top of the file. Find that "
        "line and change it to read exactly 'SECRET_PASSWORD=changed123'. Leave every other "
        "line exactly as it is."
    ),
    setup_files={
        "config.txt": "\n".join(
            "SECRET_PASSWORD=hunter2" if i == _PAGINATION_FILE_MARKER_LINE else f"row {i:03d}"
            for i in range(1, _PAGINATION_FILE_TOTAL_LINES + 1)
        ) + "\n",
    },
    check=_check_read_file_paginates_past_max_lines,
    expected_tool_calls=3,
)


_MANY_EDITS_FILE_TOTAL_LINES = 300
_MANY_EDITS_INSERT_AFTER_LINES = [10, 90, 150, 210, 260, 295]
"""Original (pre-edit) line numbers to insert a new line after. Each insertion shifts every
line below it down by one, so applying these top-to-bottom without re-reading in between
targets the wrong line for every insertion after the first — see EditFileTool's docstring
recommendation to instead go bottom-to-top."""


def _check_edit_file_many_ops_bottom_to_top(workspace_root: Path, session: Session) -> str | None:
    insert_after = set(_MANY_EDITS_INSERT_AFTER_LINES)
    expected: list[str] = []
    for i in range(1, _MANY_EDITS_FILE_TOTAL_LINES + 1):
        expected.append(f"item {i:03d}")
        if i in insert_after:
            expected.append("NOTE: reviewed")
    return _first_mismatch(expected, _lines(workspace_root / "manifest.txt"), "manifest.txt")


EDIT_FILE_MANY_OPS_BOTTOM_TO_TOP = EvalCase(
    name="edit_file_many_ops_bottom_to_top",
    prompt=(
        f"manifest.txt lists {_MANY_EDITS_FILE_TOTAL_LINES} entries, one per line, reading "
        f"'item 001' through 'item {_MANY_EDITS_FILE_TOTAL_LINES:03d}' in order (each zero-"
        "padded to 3 digits, matching its line number). Insert a new line reading "
        "'NOTE: reviewed' immediately after each of these lines, without modifying the item "
        "lines themselves or inserting anything else: "
        + ", ".join(f"item {n:03d} (line {n})" for n in _MANY_EDITS_INSERT_AFTER_LINES) + "."
    ),
    setup_files={
        "manifest.txt": "\n".join(
            f"item {i:03d}" for i in range(1, _MANY_EDITS_FILE_TOTAL_LINES + 1)
        ) + "\n",
    },
    check=_check_edit_file_many_ops_bottom_to_top,
    # +2: a competent run may re-ReadFile around a line whose EditFile response reports
    # line_hint_matched=False, to confirm the drift search landed on the right target.
    expected_tool_calls=len(_MANY_EDITS_INSERT_AFTER_LINES) + 2,
)


def _check_replace_all_case_insensitive(workspace_root: Path, session: Session) -> str | None:
    expected = [
        "The wolf jumped over the fence.",
        "wolf tracks were everywhere in the snow.",
        "A wolf call echoed at night.",
        "The dog chased something but not a wolf.",
    ]
    return _first_mismatch(expected, _lines(workspace_root / "wildlife.txt"), "wildlife.txt")


REPLACE_ALL_CASE_INSENSITIVE = EvalCase(
    name="replace_all_case_insensitive",
    prompt=(
        "In wildlife.txt, replace every occurrence of the word 'fox' with 'wolf', matching "
        "regardless of capitalization — so 'fox', 'FOX', and 'Fox' should all be replaced."
    ),
    setup_files={
        "wildlife.txt": (
            "The fox jumped over the fence.\n"
            "FOX tracks were everywhere in the snow.\n"
            "A Fox call echoed at night.\n"
            "The dog chased something but not a fox.\n"
        ),
    },
    check=_check_replace_all_case_insensitive,
    expected_tool_calls=2,
)


def _check_edit_file_ambiguous_match_first_line_repeated(
    workspace_root: Path, session: Session,
) -> str | None:
    expected = ["STATUS: done", "STATUS: pending", "STATUS: pending", "END"]
    return _first_mismatch(expected, _lines(workspace_root / "log.txt"), "log.txt")


EDIT_FILE_AMBIGUOUS_MATCH_FIRST_LINE_REPEATED = EvalCase(
    name="edit_file_ambiguous_match_first_line_repeated",
    prompt=(
        "log.txt starts with three identical lines reading 'STATUS: pending', followed by a "
        "line reading 'END'. Change ONLY the first of those three identical lines to read "
        "'STATUS: done' — leave the second and third 'STATUS: pending' lines, and the 'END' "
        "line, exactly as they are."
    ),
    setup_files={
        "log.txt": "STATUS: pending\nSTATUS: pending\nSTATUS: pending\nEND\n",
    },
    check=_check_edit_file_ambiguous_match_first_line_repeated,
    expected_tool_calls=2,
)


def _check_edit_file_ambiguous_match_last_line_repeated(workspace_root: Path, session: Session) -> str | None:
    expected = ["START", "STATUS: pending", "STATUS: pending", "STATUS: done"]
    return _first_mismatch(expected, _lines(workspace_root / "log.txt"), "log.txt")


EDIT_FILE_AMBIGUOUS_MATCH_LAST_LINE_REPEATED = EvalCase(
    name="edit_file_ambiguous_match_last_line_repeated",
    prompt=(
        "log.txt starts with a line reading 'START', followed by three identical lines "
        "reading 'STATUS: pending'. Change ONLY the last of those three identical lines to "
        "read 'STATUS: done' — leave the first two 'STATUS: pending' lines, and the 'START' "
        "line, exactly as they are."
    ),
    setup_files={
        "log.txt": "START\nSTATUS: pending\nSTATUS: pending\nSTATUS: pending\n",
    },
    check=_check_edit_file_ambiguous_match_last_line_repeated,
    expected_tool_calls=2,
)


def _check_edit_file_ambiguous_match_middle_of_repeats(workspace_root: Path, session: Session) -> str | None:
    expected = ["BEFORE", "STATUS: pending", "STATUS: done", "STATUS: pending", "AFTER"]
    return _first_mismatch(expected, _lines(workspace_root / "log.txt"), "log.txt")


EDIT_FILE_AMBIGUOUS_MATCH_MIDDLE_OF_REPEATS = EvalCase(
    name="edit_file_ambiguous_match_middle_of_repeats",
    prompt=(
        "log.txt has a line reading 'BEFORE', then three identical lines reading "
        "'STATUS: pending', then a line reading 'AFTER'. Change ONLY the middle one of those "
        "three identical lines to read 'STATUS: done' — leave the first and third "
        "'STATUS: pending' lines, plus 'BEFORE' and 'AFTER', exactly as they are."
    ),
    setup_files={
        "log.txt": "BEFORE\nSTATUS: pending\nSTATUS: pending\nSTATUS: pending\nAFTER\n",
    },
    check=_check_edit_file_ambiguous_match_middle_of_repeats,
    expected_tool_calls=2,
)


def _check_edit_file_ambiguous_match_four_repeats_inner_position(
    workspace_root: Path, session: Session,
) -> str | None:
    expected = [
        "BEFORE", "STATUS: pending", "STATUS: done", "STATUS: pending", "STATUS: pending", "AFTER",
    ]
    return _first_mismatch(expected, _lines(workspace_root / "log.txt"), "log.txt")


EDIT_FILE_AMBIGUOUS_MATCH_FOUR_REPEATS_INNER_POSITION = EvalCase(
    name="edit_file_ambiguous_match_four_repeats_inner_position",
    prompt=(
        "log.txt has a line reading 'BEFORE', then four identical lines reading "
        "'STATUS: pending', then a line reading 'AFTER'. Change ONLY the second of those four "
        "identical lines to read 'STATUS: done' — leave the first, third, and fourth "
        "'STATUS: pending' lines, plus 'BEFORE' and 'AFTER', exactly as they are."
    ),
    setup_files={
        "log.txt": (
            "BEFORE\nSTATUS: pending\nSTATUS: pending\nSTATUS: pending\nSTATUS: pending\nAFTER\n"
        ),
    },
    check=_check_edit_file_ambiguous_match_four_repeats_inner_position,
    expected_tool_calls=2,
)


def _check_list_dir_root_inventory(workspace_root: Path, session: Session) -> str | None:
    expected = ["DIRS:", "banana", "mango", "FILES:", "apple.txt", "zebra.txt"]
    return _first_mismatch(expected, _lines(workspace_root / "inventory.txt"), "inventory.txt")


LIST_DIR_ROOT_INVENTORY = EvalCase(
    name="list_dir_root_inventory",
    prompt=(
        "List every file and subdirectory directly inside the project root (do not look inside "
        "any subdirectory). Then create a file named inventory.txt containing exactly six "
        "lines, in this order: the literal line 'DIRS:', then each subdirectory name "
        "alphabetically sorted one per line, then the literal line 'FILES:', then each "
        "top-level file name alphabetically sorted one per line."
    ),
    setup_files={
        "zebra.txt": "z\n",
        "apple.txt": "a\n",
        "mango/README.md": "mango notes\n",
        "banana/note.txt": "banana notes\n",
    },
    check=_check_list_dir_root_inventory,
    expected_tool_calls=2,
)


_DATA_FILE_NAMES = ["alpha.csv", "beta.log", "gamma.json", "delta.txt", "epsilon.md"]


def _check_list_dir_count_files_in_subdirectory(workspace_root: Path, session: Session) -> str | None:
    path = workspace_root / "count.txt"
    if not path.exists():
        return "count.txt was not created"
    content = _read(path).strip()
    expected = str(len(_DATA_FILE_NAMES))
    if content != expected:
        return f"count.txt should contain exactly {expected!r}, got {content!r}"
    return None


LIST_DIR_COUNT_FILES_IN_SUBDIRECTORY = EvalCase(
    name="list_dir_count_files_in_subdirectory",
    prompt=(
        "The data/ directory contains an unknown number of files (and no subdirectories). "
        "Count exactly how many files it contains and write that count into count.txt as a "
        "single line containing only the digit(s), nothing else."
    ),
    setup_files={f"data/{name}": "x\n" for name in _DATA_FILE_NAMES},
    check=_check_list_dir_count_files_in_subdirectory,
    expected_tool_calls=2,
)


def _check_list_dir_find_subdirectory_with_marker(workspace_root: Path, session: Session) -> str | None:
    path = workspace_root / "answer.txt"
    if not path.exists():
        return "answer.txt was not created"
    content = _read(path).strip()
    expected = "2024"
    if content != expected:
        return f"answer.txt should contain exactly {expected!r}, got {content!r}"
    return None


LIST_DIR_FIND_SUBDIRECTORY_WITH_MARKER = EvalCase(
    name="list_dir_find_subdirectory_with_marker",
    prompt=(
        "Under reports/, there are several year subdirectories. Exactly one of them contains a "
        "file named summary.txt (the others don't). Find which year that is and write just "
        "that year into answer.txt, nothing else."
    ),
    setup_files={
        "reports/2023/notes.txt": "notes for 2023\n",
        "reports/2024/notes.txt": "notes for 2024\n",
        "reports/2024/summary.txt": "summary for 2024\n",
        "reports/2025/notes.txt": "notes for 2025\n",
    },
    check=_check_list_dir_find_subdirectory_with_marker,
    # ListDir(reports) + ListDir each of 3 year subdirs (or equivalent ReadFile probes) +
    # CreateFile(answer.txt).
    expected_tool_calls=5,
)


def _check_read_skill_file_extracts_marker(workspace_root: Path, session: Session) -> str | None:
    path = workspace_root / "answer.txt"
    if not path.exists():
        return "answer.txt was not created"
    content = _read(path).strip()
    expected = "QW7RANGE42"
    if expected not in content:
        return f"answer.txt should contain the calibration code {expected!r}, got {content!r}"
    return None


# The calibration code lives only in the skill's supporting file (reference/calibration.txt),
# never in the prompt or in SKILL.md, so the model can only produce it by activating the skill and
# reading that file with ReadSkillFile. The skill is pre-allowed (headless eval, no ask surface)
# and the workspace trusted so the workspace-tier skill is discoverable.
READ_SKILL_FILE_EXTRACTS_MARKER = EvalCase(
    name="read_skill_file_extracts_marker",
    prompt=(
        "Use the sensor-calibration skill to look up the calibration code for the sensor, then "
        "write just that code into answer.txt, nothing else."
    ),
    setup_files={
        ".klorb/skills/sensor-calibration/SKILL.md": (
            "---\n"
            "description: Look up the calibration code for a sensor.\n"
            "---\n\n"
            "# Sensor calibration\n\n"
            "The sensor's calibration code is recorded in the supporting file "
            "`reference/calibration.txt`. To answer a calibration-code question, read that file "
            "and report the code exactly as written there.\n"
        ),
        ".klorb/skills/sensor-calibration/reference/calibration.txt": (
            "Sensor calibration code: QW7RANGE42\n"
        ),
    },
    workspace_trusted=True,
    skill_rules=SkillRules(allow=[("workspace", "sensor-calibration")]),
    check=_check_read_skill_file_extracts_marker,
    # ActivateSkill(sensor-calibration) + ReadSkillFile(reference/calibration.txt) +
    # CreateFile(answer.txt).
    expected_tool_calls=3,
)


def _check_edit_file_single_line_shortcut(workspace_root: Path, session: Session) -> str | None:
    expected = ["Hello there", "Goodbye"]
    mismatch = _first_mismatch(expected, _lines(workspace_root / "greeting.txt"), "greeting.txt")
    if mismatch is not None:
        return mismatch
    edit_calls = _tool_call_args(session, "EditFile")
    if not any(
        not call.get("end_text")
        and ("end_line" not in call or call.get("start_line") == call.get("end_line"))
        for call in edit_calls
    ):
        return (
            "expected at least one EditFile call to use the single-line shortcut "
            "(end_text omitted or empty, with end_line either omitted entirely or equal to "
            "start_line) rather than repeating start_text as end_text"
        )
    return None


EDIT_FILE_SINGLE_LINE_SHORTCUT = EvalCase(
    name="edit_file_single_line_shortcut",
    prompt=(
        "In greeting.txt, change line 1 (currently 'Hello world') to instead read "
        "'Hello there'. Leave every other line exactly as it is."
    ),
    setup_files={"greeting.txt": "Hello world\nGoodbye\n"},
    check=_check_edit_file_single_line_shortcut,
    expected_tool_calls=2,
)


def _check_edit_file_old_text_small_block(workspace_root: Path, session: Session) -> str | None:
    expected = [
        "def total(items):", "    result = sum(items)", "    return result", "",
        "print(total([1, 2, 3]))",
    ]
    return _first_mismatch(expected, _lines(workspace_root / "notes.py"), "notes.py")


def _soft_check_edit_file_old_text_small_block(workspace_root: Path, session: Session) -> str | None:
    """The file state is correct either way; this case exists to prove old_text (with end_line
    inferred from its line count) is reachable for a short block, so a run that reaches the
    right file via the classic start_text/end_text form instead is still a pass -- just
    flagged conditional rather than treated as a failure."""
    edit_calls = _tool_call_args(session, "EditFile")
    if any(call.get("old_text") and "end_line" not in call for call in edit_calls):
        return None
    return (
        "no EditFile call replaced the block with old_text (end_line omitted, inferred from "
        "its line count) -- this case exists to prove that form is reachable for a short "
        "block; a different-but-valid strategy (e.g. classic start_text/end_text) reached the "
        "right file without exercising it"
    )


EDIT_FILE_OLD_TEXT_SMALL_BLOCK = EvalCase(
    name="edit_file_old_text_small_block",
    prompt=(
        "notes.py defines a 3-line total() function that always returns 0 instead of summing "
        "its argument. Replace those exact 3 lines with:\n"
        "def total(items):\n"
        "    result = sum(items)\n"
        "    return result\n"
        "Leave the blank line and the print statement below it exactly as they are."
    ),
    setup_files={
        "notes.py": "def total(items):\n    result = 0\n    return result\n\nprint(total([1, 2, 3]))\n",
    },
    check=_check_edit_file_old_text_small_block,
    soft_check=_soft_check_edit_file_old_text_small_block,
    expected_tool_calls=3,
)


def _check_edit_file_old_text_drifted(workspace_root: Path, session: Session) -> str | None:
    expected = [
        "# HEADER", "# inserted by edit", "def greet(name):", "    msg = 'Hi ' + name",
        "    return msg", "", "print(greet('world'))",
    ]
    return _first_mismatch(expected, _lines(workspace_root / "app.py"), "app.py")


def _soft_check_edit_file_old_text_drifted(workspace_root: Path, session: Session) -> str | None:
    """The file state is correct either way; this case exists to prove `old_text` still
    resolves via drift + full-block verification after an earlier insertion shifts its line
    numbers, so a run that reaches the right file *without* exercising that (e.g. editing
    bottom-to-top, avoiding drift entirely, or targeting just the changed line directly) is
    still a pass -- just flagged conditional rather than treated as a failure."""
    edit_calls = _tool_call_args(session, "EditFile")
    if any(call.get("old_text") for call in edit_calls):
        return None
    return (
        "no EditFile call used old_text -- this case exists to prove old_text still resolves "
        "via drift + full-block verification after an earlier insertion shifts its line "
        "numbers; a different-but-valid strategy reached the right file without exercising it"
    )


EDIT_FILE_OLD_TEXT_DRIFTED = EvalCase(
    name="edit_file_old_text_drifted",
    prompt=(
        "app.py has a header comment on line 1, then a 3-line greet() function definition, a "
        "blank line, and a print statement. Make two changes: first, insert a new line right "
        "after the header comment reading '# inserted by edit'. Second, replace the greet() "
        "function's whole 3-line body (the def line, the msg line, and the return line) so its "
        "message starts with 'Hi ' instead of 'Hello ' (leave the parameter name and return "
        "logic unchanged). Leave the blank line and print statement exactly as they are."
    ),
    setup_files={
        "app.py": (
            "# HEADER\ndef greet(name):\n    msg = 'Hello ' + name\n    return msg\n\n"
            "print(greet('world'))\n"
        ),
    },
    check=_check_edit_file_old_text_drifted,
    soft_check=_soft_check_edit_file_old_text_drifted,
    expected_tool_calls=4,
)


_REPEATED_BLOCK_FILLER_LINES = 25
"""More filler lines than EditFile's default drift search radius (20, see
DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS), so a start_line hint near the first configure() block
can't accidentally also reach the second, identical one."""

_REPEATED_BLOCK_CONTENT = (
    "# top marker\n"
    "def configure():\n"
    "    value = 0\n"
    "    return value\n"
    + "".join(f"# filler {i}\n" for i in range(1, _REPEATED_BLOCK_FILLER_LINES + 1))
    + "def configure():\n"
    "    value = 0\n"
    "    return value\n"
)


def _check_edit_file_old_text_repeated_block(workspace_root: Path, session: Session) -> str | None:
    lines = _lines(workspace_root / "config.py")
    expected_first = ["def configure():", "    value = 1", "    return value"]
    expected_second = ["def configure():", "    value = 0", "    return value"]
    if lines[1:4] != expected_first:
        return f"the first configure() block (lines 2-4) should read {expected_first!r}, got {lines[1:4]!r}"
    if lines[-3:] != expected_second:
        return (
            f"the second, distant configure() block should be unchanged {expected_second!r}, "
            f"got {lines[-3:]!r}"
        )
    return None


EDIT_FILE_OLD_TEXT_REPEATED_BLOCK = EvalCase(
    name="edit_file_old_text_repeated_block",
    prompt=(
        "config.py defines configure() near the top of the file (returning value = 0), and, "
        "much further down, a second, identical-looking configure() block that must NOT be "
        "touched. Change ONLY the first configure() function (the one near the top) so it "
        "returns value = 1 instead of value = 0. Leave every other line, including the second "
        "configure() block further down, exactly as it is."
    ),
    setup_files={"config.py": _REPEATED_BLOCK_CONTENT},
    check=_check_edit_file_old_text_repeated_block,
    expected_tool_calls=3,
)


FILE_TOOLS_CASES: list[EvalCase] = [
    CREATE_FILE_BASIC,
    CREATE_FILE_NESTED_DIRECTORY,
    READ_THEN_EDIT_SINGLE_LINE,
    REPLACE_ALL_LITERAL,
    EDIT_FILE_INSERT_LINE,
    AVOID_OVERWRITE_EXISTING_FILE,
    EDIT_FILE_MIDDLE_LINE_OF_MULTILINE_FILE,
    EDIT_FILE_LAST_LINE_OF_MULTILINE_FILE,
    EDIT_FILE_LINE_CONTENT_STARTS_WITH_DIGIT,
    EDIT_FILE_TWO_EDITS_IN_ONE_TURN,
    EDIT_FILE_COPY_LINE_CONTENT_NO_PREFIX_LEAK,
    READ_FILE_PAGINATES_PAST_MAX_LINES,
    EDIT_FILE_MANY_OPS_BOTTOM_TO_TOP,
    REPLACE_ALL_CASE_INSENSITIVE,
    EDIT_FILE_AMBIGUOUS_MATCH_FIRST_LINE_REPEATED,
    EDIT_FILE_AMBIGUOUS_MATCH_LAST_LINE_REPEATED,
    EDIT_FILE_AMBIGUOUS_MATCH_MIDDLE_OF_REPEATS,
    EDIT_FILE_AMBIGUOUS_MATCH_FOUR_REPEATS_INNER_POSITION,
    LIST_DIR_ROOT_INVENTORY,
    LIST_DIR_COUNT_FILES_IN_SUBDIRECTORY,
    LIST_DIR_FIND_SUBDIRECTORY_WITH_MARKER,
    READ_SKILL_FILE_EXTRACTS_MARKER,
    EDIT_FILE_SINGLE_LINE_SHORTCUT,
    EDIT_FILE_OLD_TEXT_SMALL_BLOCK,
    EDIT_FILE_OLD_TEXT_DRIFTED,
    EDIT_FILE_OLD_TEXT_REPEATED_BLOCK,
]

FILE_TOOLS = EvalSuite(name="file-tools", cases=FILE_TOOLS_CASES)

ALL_SUITES: list[EvalSuite] = [FILE_TOOLS]
"""Every known `EvalSuite`, keyed by `EvalSuite.name` for `run_evals.py`'s `--suite`/
`--list-suites` flags -- append a new suite here as one more `EvalSuite` entry when a
scenario group doesn't belong under `file-tools` (e.g. a future suite covering non-file
tools)."""
