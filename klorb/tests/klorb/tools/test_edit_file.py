# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.edit_file."""
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.edit_file import EditFileTool
from klorb.tools.read_file import ReadFileTool
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace

_AWS_KEY = "AKIAABCDEFGHIJKLMNOP"


def _context(
    workspace_root: Path, *, read_dirs: DirRules | None = None, write_dirs: DirRules | None = None,
    session: Session | None = None,
) -> ToolSetupContext:
    """Defaults both `readDirs`/`writeDirs` to allowing all of `workspace_root`, since
    `evaluate_write()` requires an explicit allow in *both* tables (see
    docs/adrs/00030-write-verdict-is-stricter-of-read-and-write-tables.md) and most tests here are
    about EditFile's own logic, not the permission system -- only the "Permission-table
    integration" tests below pass an explicit override to narrow that default."""
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(
            workspace=Workspace(path=workspace_root),
            read_dirs=read_dirs or DirRules(allow=[workspace_root]),
            write_dirs=write_dirs or DirRules(allow=[workspace_root])),
        session=session)


def _session(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> Session:
    config = make_session_config(
        workspace=Workspace(path=tmp_path), read_dirs=DirRules(allow=[tmp_path]),
        write_dirs=DirRules(allow=[tmp_path]))
    return Session(config=config)


def _write(path: Path, name: str, content: str) -> Path:
    file_path = path / name
    file_path.write_text(content)
    return file_path


# --- old_text: single-block replace ---


def test_replaces_a_multiline_block(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\nd\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "b\nc", "new_text": "B\nC",
    })

    assert file_path.read_text() == "a\nB\nC\nd\n"
    assert result["new_total_lines"] == 4
    assert result["post_edit_content"] == "2|B\n3|C"
    assert result["replaced_lines"] == 2


def test_replaces_a_single_line(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "a", "new_text": "A",
    })

    assert file_path.read_text() == "A\nb\nc\n"


def test_insert_by_folding_original_line_into_new_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "b", "new_text": "x\nb",
    })

    assert file_path.read_text() == "a\nx\nb\nc\n"


def test_delete_via_empty_new_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "b", "new_text": "",
    })

    assert file_path.read_text() == "a\nc\n"
    assert result["new_total_lines"] == 2


def test_delete_all_lines_leaves_empty_file(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "a\nb\nc", "new_text": "",
    })

    assert file_path.read_text() == ""


def test_whole_file_replacement(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "a\nb\nc", "new_text": "x\ny",
    })

    assert file_path.read_text() == "x\ny\n"


# --- old_text="": create/insert-into-empty sentinel ---


def test_insert_into_empty_file(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "", "new_text": "hello\nworld",
    })

    assert file_path.read_text() == "hello\nworld\n"
    assert result["new_total_lines"] == 2


def test_insert_into_existing_empty_file_does_not_report_created(tmp_path: Path) -> None:
    """created is only set when EditFile itself brought the file into existence -- an
    already-existing (but empty) file being filled in is not a creation."""
    file_path = _write(tmp_path, "sample.txt", "")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "", "new_text": "hello",
    })

    assert "created" not in result


def test_old_text_empty_on_non_empty_file_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\n")

    with pytest.raises(ValueError, match=r'old_text="" only matches when .* is missing or empty'):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": "", "new_text": "x",
        })

    assert file_path.read_text() == "a\nb\n"


def test_edit_file_auto_creates_nonexistent_file_via_empty_old_text(tmp_path: Path) -> None:
    """A filename with nothing on disk at all is treated exactly like an existing-but-empty
    file for old_text="" -- no prior CreateFile call needed."""
    file_path = tmp_path / "new.txt"
    assert not file_path.exists()

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "", "new_text": "hello\nworld",
    })

    assert file_path.read_text() == "hello\nworld\n"
    assert result["created"] is True
    assert result["new_total_lines"] == 2


def test_edit_file_auto_create_creates_missing_parent_directories(tmp_path: Path) -> None:
    file_path = tmp_path / "sub" / "dir" / "new.txt"
    assert not file_path.parent.exists()

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "", "new_text": "hello",
    })

    assert file_path.read_text() == "hello\n"
    assert result["created"] is True


def test_edit_file_nonexistent_file_other_shape_raises_with_create_hint(tmp_path: Path) -> None:
    """Only old_text="" can create a file -- any other shape against a nonexistent file fails
    closed, naming CreateFile rather than the bare OS errno text."""
    file_path = tmp_path / "missing.txt"

    with pytest.raises(FileNotFoundError, match="does not exist; use CreateFile"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": "a", "new_text": "b",
        })

    assert not file_path.exists()


# --- Trailing-newline handling ---


def test_edit_touching_eof_always_terminates_with_newline_regardless_of_original(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc")  # no trailing newline

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "c", "new_text": "z",
    })

    assert file_path.read_text() == "a\nb\nz\n"


def test_edit_touching_eof_ignores_trailing_newline_in_new_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "c", "new_text": "z\n",
    })

    assert file_path.read_text() == "a\nb\nz\n"


def test_edit_not_touching_eof_preserves_original_trailing_newline_absent(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc")  # no trailing newline

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "a", "new_text": "A",
    })

    assert file_path.read_text() == "A\nb\nc"


def test_edit_not_touching_eof_preserves_original_trailing_newline_present(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "a", "new_text": "A",
    })

    assert file_path.read_text() == "A\nb\nc\n"


# --- old_text: not found / ambiguous ---


def test_old_text_not_found_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\nd\n")

    with pytest.raises(ValueError, match=r"old_text does not match any location in the 4-line"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": "NOPE", "new_text": "X",
        })

    assert file_path.read_text() == "a\nb\nc\nd\n"


def test_old_text_ambiguous_raises_with_ready_to_use_candidates(tmp_path: Path) -> None:
    """1 line on each side is already enough to tell the two candidates apart, so the adaptive
    growth (see _minimal_disambiguating_n) stops there rather than always showing more."""
    file_path = _write(tmp_path, "sample.txt", "a\nDUP\nb\nDUP\nc\n")

    with pytest.raises(ValueError, match=r"Ambiguous match: old_text matches 2 locations") as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": "DUP", "new_text": "REPLACED",
        })

    message = str(excinfo.value)
    first = json.dumps({"old_text": "a\nDUP\nb", "new_text": "a\nREPLACED\nb"})
    second = json.dumps({"old_text": "b\nDUP\nc", "new_text": "b\nREPLACED\nc"})
    assert first in message
    assert second in message
    assert file_path.read_text() == "a\nDUP\nb\nDUP\nc\n"  # untouched


def test_old_text_ambiguous_candidate_is_directly_usable(tmp_path: Path) -> None:
    """Retrying with one of the ambiguous error's own candidate fragments succeeds."""
    file_path = _write(tmp_path, "sample.txt", "a\nDUP\nb\nDUP\nc\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "a\nDUP\nb", "new_text": "a\nREPLACED\nb",
    })

    assert file_path.read_text() == "a\nREPLACED\nb\nDUP\nc\n"
    assert result["start_line"] == 1


def test_old_text_ambiguous_at_file_boundary_clips_the_window(tmp_path: Path) -> None:
    """A candidate at the very first line has no preceding line to show -- its candidate window
    is clipped to what's actually there, not padded."""
    file_path = _write(tmp_path, "sample.txt", "DUP\nDUP\nX\n")

    with pytest.raises(ValueError, match=r"Ambiguous match: old_text matches 2 locations") as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": "DUP", "new_text": "REPLACED",
        })

    message = str(excinfo.value)
    first = json.dumps({"old_text": "DUP\nDUP", "new_text": "REPLACED\nDUP"})
    second = json.dumps({"old_text": "DUP\nDUP\nX", "new_text": "DUP\nREPLACED\nX"})
    assert first in message
    assert second in message


def test_old_text_ambiguous_four_repeats_needs_two_lines_of_context(tmp_path: Path) -> None:
    """The second of four identical lines is adjacent to another repeat on both sides at
    distance 1, so growth must reach 2 lines (as far as the unique BEFORE/AFTER anchors) before
    every candidate is uniquely distinguishable."""
    file_path = _write(tmp_path, "sample.txt", "BEFORE\nDUP\nDUP\nDUP\nDUP\nAFTER\n")

    with pytest.raises(ValueError, match=r"Ambiguous match: old_text matches 4 locations") as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": "DUP", "new_text": "REPLACED",
        })

    message = str(excinfo.value)
    second_candidate = json.dumps({
        "old_text": "BEFORE\nDUP\nDUP\nDUP\nDUP", "new_text": "BEFORE\nDUP\nREPLACED\nDUP\nDUP",
    })
    assert second_candidate in message


def test_old_text_interior_line_mismatch_leaves_no_candidate(tmp_path: Path) -> None:
    """A block whose first and last lines match but whose interior differs from the file has no
    valid candidate anywhere -- old_text is verified in full, not just at its endpoints."""
    file_path = _write(tmp_path, "sample.txt", "a\nb\nWRONG\nd\n")

    with pytest.raises(ValueError, match=r"old_text does not match any location"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": "b\nc\nd", "new_text": "B\nC\nD",
        })

    assert file_path.read_text() == "a\nb\nWRONG\nd\n"


# --- Fuzzy fallback: whitespace and dash/quote punctuation ---


def test_old_text_fuzzy_whitespace_resolves(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\n  b  \nc\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "b", "new_text": "B",
    })

    assert file_path.read_text() == "a\nB\nc\n"
    assert result["fuzzy_whitespace_match"] is True
    assert "whitespace" in result["whitespace"] or "punctuation" in result["whitespace"]


@pytest.mark.parametrize(("file_dash", "anchor_dash"), [("—", "-"), ("-", "–"), ("−", "-")])
def test_old_text_fuzzy_dash_variant_resolves(tmp_path: Path, file_dash: str, anchor_dash: str) -> None:
    file_path = _write(tmp_path, "sample.txt", f"a{file_dash}b\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": f"a{anchor_dash}b", "new_text": "REPLACED",
    })

    assert file_path.read_text() == "REPLACED\n"
    assert result["fuzzy_whitespace_match"] is True


def test_old_text_fuzzy_quote_variant_resolves(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "he said “hi”\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": 'he said "hi"', "new_text": "REPLACED",
    })

    assert file_path.read_text() == "REPLACED\n"
    assert result["fuzzy_whitespace_match"] is True


def test_exact_match_is_preferred_over_fuzzy(tmp_path: Path) -> None:
    """When an exact match exists, the fuzzy fallback never even runs -- no
    fuzzy_whitespace_match flag on an ordinary exact hit."""
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "b", "new_text": "B",
    })

    assert "fuzzy_whitespace_match" not in result


# --- old_text_start / old_text_end: span replace ---


def test_old_text_start_end_replaces_the_span(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\nd\ne\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text_start": "b", "old_text_end": "d",
        "new_text": "B\nC\nD",
    })

    assert file_path.read_text() == "a\nB\nC\nD\ne\n"
    assert result["replaced_lines"] == 3


def test_old_text_start_end_replaces_adjacent_span_with_no_interior(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text_start": "a", "old_text_end": "b",
        "new_text": "a\nBETWEEN\nb",
    })

    assert file_path.read_text() == "a\nBETWEEN\nb\nc\n"


def test_old_text_start_not_found_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match=r"old_text_start does not match any location"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text_start": "NOPE", "old_text_end": "c",
            "new_text": "X",
        })


def test_old_text_end_not_found_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match=r"old_text_end does not match any location"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text_start": "a", "old_text_end": "NOPE",
            "new_text": "X",
        })


def test_old_text_end_before_start_raises(tmp_path: Path) -> None:
    """old_text_end matches only before old_text_start's own match -- no valid pairing exists."""
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match=r"old_text_end never occurs at or after old_text_start"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text_start": "c", "old_text_end": "a",
            "new_text": "X",
        })


def test_old_text_start_end_ambiguous_raises_with_ready_to_use_candidates(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "DUP\nx\nEND\nDUP\ny\nEND\n")

    with pytest.raises(
        ValueError, match=r"old_text_start/old_text_end together match 2 locations",
    ) as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text_start": "DUP", "old_text_end": "END",
            "new_text": "REPLACED",
        })

    message = str(excinfo.value)
    first = json.dumps({
        "old_text_start": "DUP", "old_text_end": "END\nDUP", "new_text": "REPLACED\nDUP",
    })
    assert first in message


def test_old_text_start_end_ambiguous_candidate_is_directly_usable(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "DUP\nx\nEND\nDUP\ny\nEND\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text_start": "DUP", "old_text_end": "END\nDUP",
        "new_text": "REPLACED\nDUP",
    })

    assert file_path.read_text() == "REPLACED\nDUP\ny\nEND\n"
    assert result["start_line"] == 1


def test_old_text_start_end_fuzzy_whitespace_resolves(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "  a  \nb\n  c  \n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text_start": "a", "old_text_end": "c",
        "new_text": "X",
    })

    assert file_path.read_text() == "X\n"
    assert result["fuzzy_whitespace_match"] is True


# --- Argument validation ---


def test_new_text_missing_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="Missing required argument: 'new_text'"):
        EditFileTool(_context(tmp_path)).apply({"filename": str(file_path), "old_text": "a"})


def test_old_text_and_old_text_start_both_present_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(
        ValueError, match="Provide old_text, or old_text_start/old_text_end, not both",
    ):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": "a", "old_text_start": "a",
            "old_text_end": "b", "new_text": "X",
        })


def test_old_text_start_without_old_text_end_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="old_text_start and old_text_end must be provided together"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text_start": "a", "new_text": "X",
        })


def test_old_text_end_without_old_text_start_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="old_text_start and old_text_end must be provided together"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text_end": "a", "new_text": "X",
        })


def test_neither_old_text_nor_old_text_start_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(
        ValueError, match="provide old_text, or old_text_start/old_text_end",
    ):
        EditFileTool(_context(tmp_path)).apply({"filename": str(file_path), "new_text": "X"})


def test_non_string_old_text_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match=r"old_text must be a string, got 123"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": 123, "new_text": "X",
        })


def test_empty_old_text_start_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="old_text_start must be non-empty"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text_start": "", "old_text_end": "b",
            "new_text": "X",
        })


def test_empty_old_text_end_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="old_text_end must be non-empty"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text_start": "a", "old_text_end": "",
            "new_text": "X",
        })


def test_path_outside_workspace_root_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("a\n")

    with pytest.raises(PermissionError):
        EditFileTool(_context(workspace)).apply({
            "filename": str(outside), "old_text": "a", "new_text": "x",
        })


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("a\n")
    link = workspace / "link.txt"
    link.symlink_to(outside)

    with pytest.raises(PermissionError):
        EditFileTool(_context(workspace)).apply({
            "filename": str(link), "old_text": "a", "new_text": "x",
        })


def test_name_and_parameters(tmp_path: Path) -> None:
    tool = EditFileTool(_context(tmp_path))
    parameters = tool.parameters()

    assert tool.name() == "EditFile"
    assert set(parameters["required"]) == {"filename", "new_text"}
    assert {"old_text", "old_text_start", "old_text_end"} <= set(parameters["properties"])
    assert not {"old_text", "old_text_start", "old_text_end"} & set(parameters["required"])


# --- Permission-table integration (see docs/specs/permissions.md) ---


def test_writedirs_deny_rejects_an_otherwise_in_workspace_edit(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(PermissionError):
        EditFileTool(_context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))).apply({
            "filename": str(file_path), "old_text": "a", "new_text": "A",
        })

    assert file_path.read_text() == "a\nb\nc\n"


def test_writedirs_ask_raises_permission_ask_required(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(PermissionAskRequired):
        EditFileTool(_context(tmp_path, write_dirs=DirRules(ask=[tmp_path]))).apply({
            "filename": str(file_path), "old_text": "a", "new_text": "A",
        })

    assert file_path.read_text() == "a\nb\nc\n"


def test_hard_workspace_boundary_wins_even_if_writedirs_allow_covers_outside(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("a\n")

    with pytest.raises(PermissionError):
        EditFileTool(_context(workspace, write_dirs=DirRules(allow=[tmp_path]))).apply({
            "filename": str(outside), "old_text": "a", "new_text": "x",
        })

    assert outside.read_text() == "a\n"


def test_writedirs_allow_alone_does_not_grant_write_without_readdirs_allow(tmp_path: Path) -> None:
    """writeDirs.allow alone does not grant write access to a path readDirs is silent on --
    write access is never more permissive than read access for the same path; see
    docs/adrs/00030-write-verdict-is-stricter-of-read-and-write-tables.md. An integration-level
    counterpart to test_permissions.py's unit-level
    test_evaluate_write_asks_when_writedirs_allows_but_readdirs_is_silent, since this file's
    other permission tests all use the (allow, allow) default context."""
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(PermissionAskRequired):
        EditFileTool(_context(
            tmp_path, read_dirs=DirRules(), write_dirs=DirRules(allow=[tmp_path]),
        )).apply({
            "filename": str(file_path), "old_text": "a", "new_text": "A",
        })

    assert file_path.read_text() == "a\nb\nc\n"


def test_klorb_dir_write_implicitly_denied_even_with_no_config(tmp_path: Path) -> None:
    klorb_dir = tmp_path / ".klorb"
    klorb_dir.mkdir()
    file_path = klorb_dir / "klorb-config.json"
    file_path.write_text("{}")

    with pytest.raises(PermissionError):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "old_text": "{}", "new_text": "{\"tampered\": true}",
        })

    assert file_path.read_text() == "{}"


# --- summary()/detail_view() (see docs/specs/terminal-repl.md) ---


def test_summary_reports_a_git_style_line_diff(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\nd\n")
    tool = EditFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "old_text": "b\nc", "new_text": "B\nC\nD"}

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Edit file: {file_path} (+3/-2)"


def test_summary_diff_count_for_a_pure_insert(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")
    tool = EditFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "old_text": "a", "new_text": "a\nnew"}

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Edit file: {file_path} (+2/-1)"


def test_summary_diff_count_for_a_pure_delete(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")
    tool = EditFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "old_text": "b", "new_text": ""}

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Edit file: {file_path} (+0/-1)"


def test_summary_on_failure_omits_the_diff_count() -> None:
    """A failed call never resolved a location, so there's no replaced_lines to report a
    count from -- the summary just names the file and the error."""
    tool = EditFileTool(_context(Path("/tmp")))
    args = {"filename": "missing.txt", "old_text": "a", "new_text": "b"}

    assert tool.summary(args, error="not found") == "Edit file: missing.txt failed: not found"


def test_detail_view_truncates_long_edited_content_to_eight_lines(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\n")
    tool = EditFileTool(_context(tmp_path))
    new_text = "\n".join(f"line{i}" for i in range(20))
    args = {"filename": str(file_path), "old_text": "a", "new_text": new_text}

    result = tool.apply(args)
    detail = json.loads(tool.detail_view(args, result))

    assert detail["result"]["post_edit_content"] == "\n".join(f"{i + 1}|line{i}" for i in range(8)) + "\n..."


# --- diff_preview() (see docs/specs/terminal-repl.md) ---


def test_diff_preview_reflects_the_applied_change(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")
    tool = EditFileTool(_context(tmp_path))
    args = {"filename": str(file_path), "old_text": "b", "new_text": "B"}

    result = tool.apply(args)
    preview = tool.diff_preview(args, result)

    assert preview is not None
    assert preview.label == tool.summary(args, result)
    kinds = [line.kind for hunk in preview.hunks for line in hunk.lines]
    assert kinds == ["context", "del", "add", "context"]


def test_diff_preview_is_none_on_failure(tmp_path: Path) -> None:
    tool = EditFileTool(_context(tmp_path))
    args = {"filename": str(tmp_path / "missing.txt"), "old_text": "x", "new_text": "y"}

    assert tool.diff_preview(args, None, "does not exist") is None


# --- Secret redaction (see docs/specs/secret-redaction.md) ---


def test_edit_file_token_round_trip_preserves_the_real_secret(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """The read/edit loop: a `[[SECRET:...]]` token echoed back in EditFile's old_text/new_text
    resolves to the real secret for matching (so the edit succeeds at all), and the real secret
    -- never the literal token text -- is what ends up written to disk."""
    session = _session(tmp_path, make_session_config)
    try:
        file_path = _write(tmp_path, "creds.env", f"AWS_ACCESS_KEY_ID={_AWS_KEY}\nfoo\n")

        read_result = ReadFileTool(_context(tmp_path, session=session)).apply(
            {"filename": str(file_path)})
        token_line = read_result["content"].splitlines()[0].split("|", 1)[1]
        assert _AWS_KEY not in token_line
        assert token_line.startswith("AWS_ACCESS_KEY_ID=[[SECRET:")

        edit_result = EditFileTool(_context(tmp_path, session=session)).apply({
            "filename": str(file_path),
            "old_text": f"{token_line}\nfoo",
            "new_text": f"{token_line}\nbar",
        })

        # The real secret, never the literal token, lands on disk.
        assert file_path.read_text() == f"AWS_ACCESS_KEY_ID={_AWS_KEY}\nbar\n"
        # The tool result never echoes the plaintext secret back either.
        assert _AWS_KEY not in edit_result["post_edit_content"]
        assert _AWS_KEY not in str(edit_result["diff"])
        assert token_line.split("=", 1)[1] in edit_result["post_edit_content"]
    finally:
        session.close()


def test_edit_file_matches_a_token_via_old_text_start_end_form(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """old_text_start/old_text_end also resolve a token, not just old_text."""
    session = _session(tmp_path, make_session_config)
    try:
        file_path = _write(tmp_path, "creds.env", f"AWS_ACCESS_KEY_ID={_AWS_KEY}\n")

        read_result = ReadFileTool(_context(tmp_path, session=session)).apply(
            {"filename": str(file_path)})
        token_line = read_result["content"].splitlines()[0].split("|", 1)[1]

        EditFileTool(_context(tmp_path, session=session)).apply({
            "filename": str(file_path),
            "old_text_start": token_line, "old_text_end": token_line,
            "new_text": "AWS_ACCESS_KEY_ID=rotated",
        })

        assert file_path.read_text() == "AWS_ACCESS_KEY_ID=rotated\n"
    finally:
        session.close()


def test_edit_file_without_a_token_behaves_normally(tmp_path: Path) -> None:
    """A redactor is always attached, but an edit that never mentions a token is unaffected."""
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "old_text": "b", "new_text": "B",
    })

    assert file_path.read_text() == "a\nB\nc\n"
