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
)


CASES: list[EvalCase] = [
    CREATE_FILE_BASIC,
    CREATE_FILE_NESTED_DIRECTORY,
    READ_THEN_EDIT_SINGLE_LINE,
    REPLACE_ALL_LITERAL,
    EDIT_FILE_INSERT_LINE,
    AVOID_OVERWRITE_EXISTING_FILE,
]
