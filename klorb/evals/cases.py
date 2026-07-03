# © Copyright 2026 Aaron Kimball
"""Synthesized EvalCase tasks covering ReadFile, CreateFile, EditFile, and ReplaceAll.

Each case's `check` inspects the resulting workspace file state only, never `final_response`
text — see docs/adrs/grade-tool-evals-by-filesystem-state.md. Content comparisons tolerate
incidental trailing-newline differences (a model may or may not end a file with a trailing
newline) but are otherwise exact: an eval case is meant to catch a model failing to use a tool
correctly, not to reward creative alternate phrasing of the outcome.

A handful of cases use files longer than `DEFAULT_READ_FILE_MAX_LINES` so a single `ReadFile`
call can't return the whole file, forcing a competent run to either page with
`start_line`/`end_line` or target specific lines directly — these also inspect
`session.messages` for the `ReadFile`/`EditFile` calls actually made (see
docs/adrs/grade-tool-evals-by-filesystem-state.md's "where useful" carve-out), not just the
resulting file content.

Another handful use repeated, identical lines at or near a file's first/last line, where
`EditFile`'s drift search (see
docs/adrs/edit-file-tolerates-bounded-line-drift-via-local-candidate-search.md) necessarily
returns more than one candidate — these check that the model actually recovers by supplying
`context_before`/`context_after` (inspecting `session.messages` for those arguments, not just
the resulting content), rather than passing only because it stumbled onto the right file state
some other way.
"""

import json
from pathlib import Path

from klorb.process_config import DEFAULT_READ_FILE_MAX_LINES
from klorb.session import Session

from .harness import EvalCase


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
    if len(read_calls) < 2:
        return (
            f"expected at least 2 ReadFile calls to page past the default "
            f"{DEFAULT_READ_FILE_MAX_LINES}-line max_lines cap and reach line "
            f"{_PAGINATION_FILE_MARKER_LINE}, got {len(read_calls)}"
        )
    return None


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
    mismatch = _first_mismatch(expected, _lines(workspace_root / "log.txt"), "log.txt")
    if mismatch is not None:
        return mismatch
    edit_calls = _tool_call_args(session, "EditFile")
    if not any(
        call.get("context_after") or call.get("context_before") == ""
        for call in edit_calls
    ):
        return (
            "expected at least one EditFile call to supply either a non-empty context_after, "
            "or context_before=\"\" (asserting nothing genuinely precedes the target -- a "
            "non-empty context_before can never match there, since nothing precedes line 1), "
            "to disambiguate the repeated first line"
        )
    return None


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
    mismatch = _first_mismatch(expected, _lines(workspace_root / "log.txt"), "log.txt")
    if mismatch is not None:
        return mismatch
    edit_calls = _tool_call_args(session, "EditFile")
    if not any(
        call.get("context_before") or call.get("context_after") == ""
        for call in edit_calls
    ):
        return (
            "expected at least one EditFile call to supply either a non-empty context_before, "
            "or context_after=\"\" (asserting nothing genuinely follows the target -- a "
            "non-empty context_after can never match there, since nothing follows the last "
            "line), to disambiguate the repeated last line"
        )
    return None


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
    mismatch = _first_mismatch(expected, _lines(workspace_root / "log.txt"), "log.txt")
    if mismatch is not None:
        return mismatch
    edit_calls = _tool_call_args(session, "EditFile")
    if not any(call.get("context_before") and call.get("context_after") for call in edit_calls):
        return (
            "expected at least one EditFile call to supply both non-empty context_before and "
            "context_after: since all three repeated lines fall within the default drift "
            "search radius, an exact start_line hint alone can't disambiguate the middle one "
            "from its neighbors -- only context on both sides can"
        )
    return None


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
    mismatch = _first_mismatch(expected, _lines(workspace_root / "log.txt"), "log.txt")
    if mismatch is not None:
        return mismatch
    edit_calls = _tool_call_args(session, "EditFile")
    if not any("\n" in (call.get("context_before") or "") for call in edit_calls):
        return (
            "expected at least one EditFile call to supply a context_before spanning 2 or "
            "more lines: the second of four repeated lines is adjacent to another repeat, not "
            "to the unique 'BEFORE' anchor, so a single line of context_before still leaves "
            "multiple candidates -- only reaching back far enough to include 'BEFORE' resolves it"
        )
    return None


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


CASES: list[EvalCase] = [
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
]
