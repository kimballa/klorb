# © Copyright 2026 Aaron Kimball
"""Synthesized EvalCase tasks covering ReadFile, CreateFile, EditFile, and ReplaceAll.

Each case's `check` inspects the resulting workspace file state only, never `final_response`
text — see docs/adrs/grade-tool-evals-by-filesystem-state.md. Content comparisons tolerate
incidental trailing-newline differences (a model may or may not end a file with a trailing
newline) but are otherwise exact: an eval case is meant to catch a model failing to use a tool
correctly, not to reward creative alternate phrasing of the outcome.
"""

from pathlib import Path

from klorb.session import Session

from .harness import EvalCase


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _lines(path: Path) -> list[str]:
    return _read(path).splitlines()


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
    expected_tool_calls=2,
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
    expected_tool_calls=2,
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
    expected_tool_calls=2,
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
]
