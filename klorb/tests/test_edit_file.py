# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.edit_file."""

import json
from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.edit_file import EditFileTool
from klorb.tools.setup_context import ToolSetupContext


def _context(
    workspace_root: Path, *, read_dirs: DirRules | None = None, write_dirs: DirRules | None = None,
    process_config: ProcessConfig | None = None,
) -> ToolSetupContext:
    """Defaults both `readDirs`/`writeDirs` to allowing all of `workspace_root`, since
    `evaluate_write()` requires an explicit allow in *both* tables (see
    docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md) and most tests here are
    about EditFile's own logic, not the permission system -- only the "Permission-table
    integration" tests below pass an explicit override to narrow that default. `process_config`
    defaults to `ProcessConfig()` (so `edit_file_drift_search_radius` is
    `DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS`) -- only the drift-search-radius tests override it."""
    return ToolSetupContext(
        process_config=process_config or ProcessConfig(),
        session_config=SessionConfig(
            workspace_root=workspace_root,
            read_dirs=read_dirs or DirRules(allow=[workspace_root]),
            write_dirs=write_dirs or DirRules(allow=[workspace_root])))


def _write(path: Path, name: str, content: str) -> Path:
    file_path = path / name
    file_path.write_text(content)
    return file_path


def test_replaces_a_line_range(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\nd\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 3,
        "start_text": "b", "end_text": "c", "new_text": "B\nC",
    })

    assert file_path.read_text() == "a\nB\nC\nd\n"
    assert result["new_total_lines"] == 4
    assert result["content"] == "2|B\n3|C"


def test_replaces_starting_at_line_one(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "a", "end_text": "a", "new_text": "A",
    })

    assert file_path.read_text() == "A\nb\nc\n"


def test_insert_via_single_line_replace(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 2,
        "start_text": "b", "end_text": "b", "new_text": "x\nb",
    })

    assert file_path.read_text() == "a\nx\nb\nc\n"


def test_delete_via_empty_new_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 2,
        "start_text": "b", "end_text": "b", "new_text": "",
    })

    assert file_path.read_text() == "a\nc\n"
    assert result["new_total_lines"] == 2


def test_delete_all_lines_leaves_empty_file(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 3,
        "start_text": "a", "end_text": "c", "new_text": "",
    })

    assert file_path.read_text() == ""


def test_insert_into_empty_file(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 0,
        "start_text": "", "end_text": "", "new_text": "hello\nworld",
    })

    assert file_path.read_text() == "hello\nworld\n"
    assert result["new_total_lines"] == 2


def test_insert_into_empty_file_rejects_nonzero_end_line(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "")

    with pytest.raises(ValueError, match="is empty"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "", "end_text": "", "new_text": "x",
        })


def test_whole_file_replacement(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 3,
        "start_text": "a", "end_text": "c", "new_text": "x\ny",
    })

    assert file_path.read_text() == "x\ny\n"


def test_edit_touching_eof_always_terminates_with_newline_regardless_of_original(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc")  # no trailing newline

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 3, "end_line": 3,
        "start_text": "c", "end_text": "c", "new_text": "z",
    })

    assert file_path.read_text() == "a\nb\nz\n"


def test_edit_touching_eof_ignores_trailing_newline_in_new_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 3, "end_line": 3,
        "start_text": "c", "end_text": "c", "new_text": "z\n",
    })

    assert file_path.read_text() == "a\nb\nz\n"


def test_edit_not_touching_eof_preserves_original_trailing_newline_absent(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc")  # no trailing newline

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "a", "end_text": "a", "new_text": "A",
    })

    assert file_path.read_text() == "A\nb\nc"


def test_edit_not_touching_eof_preserves_original_trailing_newline_present(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "a", "end_text": "a", "new_text": "A",
    })

    assert file_path.read_text() == "A\nb\nc\n"


def test_drift_verification_failure_on_start_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="start_text does not match"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "STALE", "end_text": "a", "new_text": "A",
        })


def test_drift_verification_failure_on_end_text(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="end_text does not match"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 2,
            "start_text": "a", "end_text": "STALE", "new_text": "A",
        })


def test_drift_within_radius_relocates_single_line_edit(tmp_path: Path) -> None:
    """A stale start_line/end_line hint (e.g. from before an earlier edit shifted the file) is
    tolerated automatically when the requested content still occurs, uniquely, within
    `edit_file_drift_search_radius` lines of the hint."""
    file_path = _write(tmp_path, "sample.txt", "p\nq\nr\ns\nt\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 2,
        "start_text": "r", "end_text": "r", "new_text": "R",
    })

    assert file_path.read_text() == "p\nq\nR\ns\nt\n"
    assert result["requested_start_line"] == 2
    assert result["requested_end_line"] == 2
    assert result["start_line"] == 3
    assert result["line_hint_matched"] is False


def test_drift_within_radius_relocates_multiline_span(tmp_path: Path) -> None:
    """Drift relocation preserves the requested span length (end_line - start_line), so a
    multi-line replacement still lands on the correct multi-line region, not just a
    single matching line."""
    file_path = _write(tmp_path, "sample.txt", "p\nq\nr\ns\nt\nu\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 3, "end_line": 4,
        "start_text": "s", "end_text": "t", "new_text": "S\nT",
    })

    assert file_path.read_text() == "p\nq\nr\nS\nT\nu\n"
    assert result["start_line"] == 4
    assert result["line_hint_matched"] is False


def test_drift_beyond_radius_still_raises(tmp_path: Path) -> None:
    """A match further away than edit_file_drift_search_radius is not found -- the tool fails
    closed rather than searching the whole file, since an unbounded search risks silently
    matching an unrelated, coincidentally-identical line far away."""
    file_path = _write(tmp_path, "sample.txt", "\n".join(f"L{i}" for i in range(1, 11)) + "\n")

    with pytest.raises(ValueError, match="start_text does not match"):
        EditFileTool(_context(
            tmp_path, process_config=ProcessConfig(edit_file_drift_search_radius=2),
        )).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "L5", "end_text": "L5", "new_text": "FIVE",
        })

    assert "L5" in file_path.read_text()  # untouched


def test_ambiguous_match_raises_when_multiple_candidates_in_radius(tmp_path: Path) -> None:
    """The error names each candidate's actual nearby lines (not just its line number), so a
    model can copy context_before/context_after verbatim rather than reconstruct them from a
    separate ReadFile call and risk miscounting which lines are truly adjacent. Here 1 line on
    each side is already enough to tell the two candidates apart, so the adaptive preview
    (see _minimal_disambiguating_window) stops there rather than always showing more."""
    file_path = _write(tmp_path, "sample.txt", "a\nDUP\nb\nDUP\nc\n")

    with pytest.raises(ValueError, match=r"Ambiguous match.*\[2, 4\]") as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 3, "end_line": 3,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        })

    message = str(excinfo.value)
    assert "line 2 (context_before='a', context_after='b')" in message
    assert "line 4 (context_before='b', context_after='c')" in message
    assert file_path.read_text() == "a\nDUP\nb\nDUP\nc\n"  # untouched


def test_ambiguous_match_resolved_via_context_before_and_after(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nDUP\nb\nDUP\nc\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 3, "end_line": 3,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_before": "a",
    })

    assert file_path.read_text() == "a\nREPLACED\nb\nDUP\nc\n"
    assert result["start_line"] == 2


def test_context_mismatch_excludes_otherwise_valid_candidate(tmp_path: Path) -> None:
    """start_text/end_text match exactly at the hinted line, but a supplied context_before
    that doesn't match there rules it out, leaving no candidate at all."""
    file_path = _write(tmp_path, "sample.txt", "a\nDUP\nb\nc\n")

    with pytest.raises(ValueError, match="context_before/context_after does not match"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 2, "end_line": 2,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
            "context_before": "WRONG",
        })

    assert file_path.read_text() == "a\nDUP\nb\nc\n"  # untouched


def test_empty_context_before_asserts_genuine_start_of_file(tmp_path: Path) -> None:
    """context_before="" is a real, checked assertion ("nothing precedes this line"), not a
    no-op -- unlike omitting the argument entirely, which would have matched this
    already-unique candidate fine. This is what actually lets context_before="" usefully
    disambiguate a genuine first line elsewhere (see
    test_ambiguous_match_at_first_line_resolved_via_empty_context_before): it must reject a
    candidate that merely looks fine otherwise but isn't truly first."""
    file_path = _write(tmp_path, "sample.txt", "a\nDUP\nb\n")

    with pytest.raises(ValueError, match="context_before/context_after does not match"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 2, "end_line": 2,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
            "context_before": "",
        })

    assert file_path.read_text() == "a\nDUP\nb\n"  # untouched


# --- Boundary cases: repeated lines at the very start/end of a file (see
# docs/adrs/edit-file-tolerates-bounded-line-drift-via-local-candidate-search.md) ---


def test_ambiguous_match_at_first_line_resolved_via_context_after(tmp_path: Path) -> None:
    """Three identical leading lines make the first one ambiguous on its own; context_after
    (a lever usable at any position, unlike context_before at the very first line) pins it
    down, given enough lines to reach past the other two repeats."""
    file_path = _write(tmp_path, "sample.txt", "DUP\nDUP\nDUP\nX\n")

    with pytest.raises(ValueError, match=r"Ambiguous match.*\[1, 2, 3\]") as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        })

    message = str(excinfo.value)
    assert "line 1 (context_before='' (nothing precedes -- start of file), context_after='DUP')" in message

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_after": "DUP\nDUP",
    })

    assert file_path.read_text() == "REPLACED\nDUP\nDUP\nX\n"
    assert result["start_line"] == 1


def test_ambiguous_match_at_first_line_resolved_via_empty_context_before(tmp_path: Path) -> None:
    """context_before="" is a distinct, stronger assertion than omitting the argument: it
    means "there is genuinely nothing before this line" (i.e. it's the file's very first
    line), which alone excludes every other candidate -- cheaper than reaching 2 lines of
    context_after to the same end, and exactly what the "Ambiguous match" error's own preview
    for line 1 recommends."""
    file_path = _write(tmp_path, "sample.txt", "DUP\nDUP\nDUP\nX\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_before": "",
    })

    assert file_path.read_text() == "REPLACED\nDUP\nDUP\nX\n"
    assert result["start_line"] == 1


def test_context_before_cannot_disambiguate_at_first_line(tmp_path: Path) -> None:
    """No line precedes line 1, so any non-empty context_before excludes it as a candidate no
    matter what it says -- only context_after can ever pin down an edit at the very start of a
    file."""
    file_path = _write(tmp_path, "sample.txt", "DUP\nDUP\nX\n")

    with pytest.raises(ValueError, match="context_before/context_after does not match"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
            "context_before": "ANYTHING",
        })

    assert file_path.read_text() == "DUP\nDUP\nX\n"  # untouched


def test_insert_before_first_line_among_repeats_via_context_after(tmp_path: Path) -> None:
    """Inserting before the very first line (the fold-original-into-new_text convention, at
    start_line=end_line=1) among repeated leading lines, disambiguated with context_after."""
    file_path = _write(tmp_path, "sample.txt", "DUP\nDUP\nX\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "DUP", "end_text": "DUP", "new_text": "NEW\nDUP",
        "context_after": "DUP\nX",
    })

    assert file_path.read_text() == "NEW\nDUP\nDUP\nX\n"
    assert result["start_line"] == 1


def test_replace_middle_of_three_identical_leading_lines_via_both_contexts(tmp_path: Path) -> None:
    """When all three repeats are within the search radius, neither an exact start_line hint
    nor either context alone can isolate the middle one -- start_line only centers the search
    window, it never filters candidates by itself, and one-sided context still leaves two
    candidates. Both context_before and context_after together are required."""
    file_path = _write(tmp_path, "sample.txt", "DUP\nDUP\nDUP\nX\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 2,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_before": "DUP", "context_after": "DUP",
    })

    assert file_path.read_text() == "DUP\nREPLACED\nDUP\nX\n"
    assert result["start_line"] == 2


def test_ambiguous_match_at_last_line_resolved_via_context_before(tmp_path: Path) -> None:
    """Mirror of the first-line case: three identical trailing lines make the last one
    ambiguous; context_before (a lever usable at any position, unlike context_after at the
    very last line) pins it down, given enough lines to reach past the other two repeats."""
    file_path = _write(tmp_path, "sample.txt", "X\nDUP\nDUP\nDUP\n")

    with pytest.raises(ValueError, match=r"Ambiguous match.*\[2, 3, 4\]") as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 4, "end_line": 4,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        })

    message = str(excinfo.value)
    assert "line 4 (context_before='DUP', context_after='' (nothing follows -- end of file))" in message

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 4, "end_line": 4,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_before": "DUP\nDUP",
    })

    assert file_path.read_text() == "X\nDUP\nDUP\nREPLACED\n"
    assert result["start_line"] == 4


def test_ambiguous_match_at_last_line_resolved_via_empty_context_after(tmp_path: Path) -> None:
    """Mirror of the empty-context_before case: context_after="" asserts "there is genuinely
    nothing after this line" (i.e. it's the file's very last line) -- cheaper than reaching 2
    lines of context_before to the same end."""
    file_path = _write(tmp_path, "sample.txt", "X\nDUP\nDUP\nDUP\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 4, "end_line": 4,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_after": "",
    })

    assert file_path.read_text() == "X\nDUP\nDUP\nREPLACED\n"
    assert result["start_line"] == 4


def test_context_after_cannot_disambiguate_at_last_line(tmp_path: Path) -> None:
    """No line follows the last line, so any non-empty context_after excludes it as a
    candidate no matter what it says -- only context_before can ever pin down an edit at the
    very end of a file."""
    file_path = _write(tmp_path, "sample.txt", "X\nDUP\nDUP\n")

    with pytest.raises(ValueError, match="context_before/context_after does not match"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 3, "end_line": 3,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
            "context_after": "ANYTHING",
        })

    assert file_path.read_text() == "X\nDUP\nDUP\n"  # untouched


def test_insert_after_last_line_among_repeats_via_context_before(tmp_path: Path) -> None:
    """Inserting after the very last line (the fold-original-into-new_text convention, at
    start_line=end_line=<last line>) among repeated trailing lines, disambiguated with
    context_before."""
    file_path = _write(tmp_path, "sample.txt", "X\nDUP\nDUP\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 3, "end_line": 3,
        "start_text": "DUP", "end_text": "DUP", "new_text": "DUP\nNEW",
        "context_before": "DUP",
    })

    assert file_path.read_text() == "X\nDUP\nDUP\nNEW\n"
    assert result["start_line"] == 3


def test_replace_middle_of_three_identical_trailing_lines_via_both_contexts(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "X\nDUP\nDUP\nDUP\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 3, "end_line": 3,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_before": "DUP", "context_after": "DUP",
    })

    assert file_path.read_text() == "X\nDUP\nREPLACED\nDUP\n"
    assert result["start_line"] == 3


# --- Four consecutive identical lines: with a fixed 2-line preview/context window, the two
# "inner" positions (adjacent to another repeat on the near side, not directly to the unique
# anchor) need the full 2 lines of context on one side -- 1 line still leaves multiple
# candidates. The two outer positions, immediately adjacent to an anchor, still only need 1.
# Three repeats alone can't exercise this: every position there is within 1 line of an anchor
# on at least one side. ---


def test_four_identical_lines_outer_position_resolved_via_single_line_context(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "BEFORE\nDUP\nDUP\nDUP\nDUP\nAFTER\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 2,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_before": "BEFORE",
    })

    assert file_path.read_text() == "BEFORE\nREPLACED\nDUP\nDUP\nDUP\nAFTER\n"
    assert result["start_line"] == 2


def test_four_identical_lines_inner_position_needs_two_lines_of_context_before(tmp_path: Path) -> None:
    """The second of four repeats is adjacent to another repeat, not to the unique 'BEFORE'
    anchor -- one line of context_before still leaves 3 candidates; only the full two-line
    preview (reaching back to 'BEFORE') resolves it."""
    file_path = _write(tmp_path, "sample.txt", "BEFORE\nDUP\nDUP\nDUP\nDUP\nAFTER\n")

    with pytest.raises(ValueError, match=r"Ambiguous match.*\[3, 4, 5\]"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 3, "end_line": 3,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
            "context_before": "DUP",
        })

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 3, "end_line": 3,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_before": "BEFORE\nDUP",
    })

    assert file_path.read_text() == "BEFORE\nDUP\nREPLACED\nDUP\nDUP\nAFTER\n"
    assert result["start_line"] == 3


def test_four_identical_lines_inner_position_needs_two_lines_of_context_after(tmp_path: Path) -> None:
    """Mirror of the context_before case: the third of four repeats is adjacent to another
    repeat, not to the unique 'AFTER' anchor -- one line of context_after still leaves 3
    candidates; only the full two-line preview (reaching forward to 'AFTER') resolves it."""
    file_path = _write(tmp_path, "sample.txt", "BEFORE\nDUP\nDUP\nDUP\nDUP\nAFTER\n")

    with pytest.raises(ValueError, match=r"Ambiguous match.*\[2, 3, 4\]"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 4, "end_line": 4,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
            "context_after": "DUP",
        })

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 4, "end_line": 4,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_after": "DUP\nAFTER",
    })

    assert file_path.read_text() == "BEFORE\nDUP\nDUP\nREPLACED\nDUP\nAFTER\n"
    assert result["start_line"] == 4


def test_four_identical_lines_other_outer_position_resolved_via_single_line_context(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "BEFORE\nDUP\nDUP\nDUP\nDUP\nAFTER\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 5, "end_line": 5,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_after": "AFTER",
    })

    assert file_path.read_text() == "BEFORE\nDUP\nDUP\nDUP\nREPLACED\nAFTER\n"
    assert result["start_line"] == 5


def test_five_identical_lines_true_middle_needs_both_contexts_two_lines_each(tmp_path: Path) -> None:
    """The true middle of five repeats is 2 lines from either boundary anchor, so neither
    side alone reaches far enough to disambiguate it (unlike a run of three or four, where
    every position is within reach of some single side) -- this is exactly the case a fixed,
    non-adaptive preview length could permanently fail to resolve; the adaptive algorithm
    (_minimal_disambiguating_window) instead grows the window on both sides until it does."""
    file_path = _write(tmp_path, "sample.txt", "BEFORE\nDUP\nDUP\nDUP\nDUP\nDUP\nAFTER\n")

    with pytest.raises(ValueError, match=r"Ambiguous match.*\[2, 3, 4, 5, 6\]") as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 4, "end_line": 4,
            "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        })

    message = str(excinfo.value)
    assert "line 4 (context_before='DUP\\nDUP', context_after='DUP\\nDUP')" in message

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 4, "end_line": 4,
        "start_text": "DUP", "end_text": "DUP", "new_text": "REPLACED",
        "context_before": "DUP\nDUP", "context_after": "DUP\nDUP",
    })

    assert file_path.read_text() == "BEFORE\nDUP\nDUP\nREPLACED\nDUP\nDUP\nAFTER\n"
    assert result["start_line"] == 4


def test_response_line_hint_matched_true_when_no_drift(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    result = EditFileTool(_context(tmp_path)).apply({
        "filename": str(file_path), "start_line": 2, "end_line": 2,
        "start_text": "b", "end_text": "b", "new_text": "B",
    })

    assert result["line_hint_matched"] is True
    assert result["requested_start_line"] == result["start_line"] == 2
    assert result["requested_end_line"] == 2


def test_out_of_range_hint_does_not_trigger_search(tmp_path: Path) -> None:
    """An out-of-bounds start_line/end_line hint raises immediately -- no drift search is
    attempted at all -- even though search isn't the thing being tested here, this pins that
    the range gate still runs first, unchanged, ahead of any candidate search."""
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="out of range"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 10,
            "start_text": "a", "end_text": "c", "new_text": "x",
        })

    assert file_path.read_text() == "a\nb\nc\n"  # untouched


def test_out_of_bounds_line_numbers_raise(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="out of range"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 10,
            "start_text": "a", "end_text": "c", "new_text": "x",
        })


def test_end_line_before_start_line_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="out of range"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 3, "end_line": 1,
            "start_text": "c", "end_text": "a", "new_text": "x",
        })


def test_non_integer_line_number_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="start_line must be an integer"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": "1", "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "x",
        })


def test_boolean_line_number_raises(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="end_line must be an integer"):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": True,
            "start_text": "a", "end_text": "a", "new_text": "x",
        })


def test_multiline_start_text_raises_with_did_you_mean_nudge(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="start_text must be exactly one line") as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 2,
            "start_text": "a\nb", "end_text": "b", "new_text": "x",
        })
    assert "Did you mean 'a'?" in str(excinfo.value)


def test_multiline_end_text_raises_with_did_you_mean_nudge(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(ValueError, match="end_text must be exactly one line") as excinfo:
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 2,
            "start_text": "a", "end_text": "b\nc", "new_text": "x",
        })
    assert "Did you mean 'b'?" in str(excinfo.value)


def test_path_outside_workspace_root_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("a\n")

    with pytest.raises(PermissionError):
        EditFileTool(_context(workspace)).apply({
            "filename": str(outside), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "x",
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
            "filename": str(link), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "x",
        })


def test_name_and_parameters(tmp_path: Path) -> None:
    tool = EditFileTool(_context(tmp_path))
    parameters = tool.parameters()

    assert tool.name() == "EditFile"
    assert set(parameters["required"]) == {
        "filename", "start_line", "end_line", "start_text", "end_text", "new_text"}
    assert {"context_before", "context_after"} <= set(parameters["properties"])
    assert not {"context_before", "context_after"} & set(parameters["required"])


# --- Permission-table integration (see docs/specs/permissions.md) ---


def test_writedirs_deny_rejects_an_otherwise_in_workspace_edit(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(PermissionError):
        EditFileTool(_context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "A",
        })

    assert file_path.read_text() == "a\nb\nc\n"


def test_writedirs_ask_raises_permission_ask_required(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(PermissionAskRequired):
        EditFileTool(_context(tmp_path, write_dirs=DirRules(ask=[tmp_path]))).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "A",
        })

    assert file_path.read_text() == "a\nb\nc\n"


def test_hard_workspace_boundary_wins_even_if_writedirs_allow_covers_outside(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("a\n")

    with pytest.raises(PermissionError):
        EditFileTool(_context(workspace, write_dirs=DirRules(allow=[tmp_path]))).apply({
            "filename": str(outside), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "x",
        })

    assert outside.read_text() == "a\n"


def test_writedirs_allow_alone_does_not_grant_write_without_readdirs_allow(tmp_path: Path) -> None:
    """writeDirs.allow alone does not grant write access to a path readDirs is silent on --
    write access is never more permissive than read access for the same path; see
    docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md. An integration-level
    counterpart to test_permissions.py's unit-level
    test_evaluate_write_asks_when_writedirs_allows_but_readdirs_is_silent, since this file's
    other permission tests all use the (allow, allow) default context."""
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")

    with pytest.raises(PermissionAskRequired):
        EditFileTool(_context(
            tmp_path, read_dirs=DirRules(), write_dirs=DirRules(allow=[tmp_path]),
        )).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "a", "end_text": "a", "new_text": "A",
        })

    assert file_path.read_text() == "a\nb\nc\n"


def test_klorb_dir_write_implicitly_denied_even_with_no_config(tmp_path: Path) -> None:
    klorb_dir = tmp_path / ".klorb"
    klorb_dir.mkdir()
    file_path = klorb_dir / "klorb-config.json"
    file_path.write_text("{}")

    with pytest.raises(PermissionError):
        EditFileTool(_context(tmp_path)).apply({
            "filename": str(file_path), "start_line": 1, "end_line": 1,
            "start_text": "{}", "end_text": "{}", "new_text": "{\"tampered\": true}",
        })

    assert file_path.read_text() == "{}"


# --- summary()/detail_view() (see docs/specs/terminal-repl.md) ---


def test_summary_reports_a_git_style_line_diff(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\nd\n")
    tool = EditFileTool(_context(tmp_path))
    args = {
        "filename": str(file_path), "start_line": 2, "end_line": 3,
        "start_text": "b", "end_text": "c", "new_text": "B\nC\nD",
    }

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Edit file: {file_path} (+3/-2)"


def test_summary_diff_count_for_a_pure_insert(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")
    tool = EditFileTool(_context(tmp_path))
    args = {
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "a", "end_text": "a", "new_text": "a\nnew",
    }

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Edit file: {file_path} (+2/-1)"


def test_summary_diff_count_for_a_pure_delete(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\nb\nc\n")
    tool = EditFileTool(_context(tmp_path))
    args = {
        "filename": str(file_path), "start_line": 2, "end_line": 2,
        "start_text": "b", "end_text": "b", "new_text": "",
    }

    result = tool.apply(args)

    assert tool.summary(args, result) == f"Edit file: {file_path} (+0/-1)"


def test_summary_on_failure_includes_the_error_and_still_computes_the_diff_count() -> None:
    tool = EditFileTool(_context(Path("/tmp")))
    args = {
        "filename": "missing.txt", "start_line": 1, "end_line": 1,
        "start_text": "a", "end_text": "a", "new_text": "b",
    }

    assert tool.summary(args, error="not found") == "Edit file: missing.txt (+1/-1) failed: not found"


def test_detail_view_truncates_long_edited_content_to_eight_lines(tmp_path: Path) -> None:
    file_path = _write(tmp_path, "sample.txt", "a\n")
    tool = EditFileTool(_context(tmp_path))
    new_text = "\n".join(f"line{i}" for i in range(20))
    args = {
        "filename": str(file_path), "start_line": 1, "end_line": 1,
        "start_text": "a", "end_text": "a", "new_text": new_text,
    }

    result = tool.apply(args)
    detail = json.loads(tool.detail_view(args, result))

    assert detail["result"]["content"] == "\n".join(f"{i + 1}|line{i}" for i in range(8)) + "\n..."
