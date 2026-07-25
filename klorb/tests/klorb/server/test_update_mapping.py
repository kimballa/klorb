# © Copyright 2026 Aaron Kimball
"""Tests for `klorb.server.update_mapping` -- pure klorb-event-to-ACP-tool-call-update mapping.
See docs/specs/klorb-server.md's tool-call update mapping section."""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirRules, canonicalize_dir
from klorb.process_config import ProcessConfig
from klorb.server.update_mapping import (
    TOOL_KIND_MAP,
    TOOL_LOCATION_ARG,
    _diff_text,
    tool_call_finished_update,
    tool_call_started_update,
)
from klorb.session import SessionConfig
from klorb.session.events import ToolCallEvent, ToolCallStartedEvent
from klorb.tools.registry import ToolRegistry
from klorb.tools.util import build_diff_hunks
from klorb.workspace import Workspace

# A future tool this dict genuinely can't classify yet would show up here, with a comment
# explaining why -- empty today, since every production tool has an explicit TOOL_KIND_MAP entry.
_UNMAPPED_OK: frozenset[str] = frozenset()


def _registry(tmp_path: Path) -> ToolRegistry:
    config = SessionConfig(
        model="some/model",
        workspace=Workspace(path=tmp_path, trusted=True),
        read_dirs=DirRules(allow=[tmp_path]),
        write_dirs=DirRules(allow=[tmp_path]),
    )
    return ToolRegistry.discover_tools(ProcessConfig(), config)


def test_kind_map_covers_every_registered_tool_name(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    unmapped = [
        tool.name() for tool in registry.tools()
        if tool.name() not in TOOL_KIND_MAP and tool.name() not in _UNMAPPED_OK
    ]
    assert unmapped == [], (
        f"Tool(s) {unmapped} have no TOOL_KIND_MAP entry and aren't in _UNMAPPED_OK -- add one "
        "of the two so a future tool doesn't silently render as ToolKind 'other'.")


def test_unknown_tool_name_falls_back_to_other_kind(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(call_id="1", name="NotARealTool", args={})

    update = tool_call_started_update(event, registry, tmp_path)

    assert update.kind == "other"
    assert update.title == "NotARealTool"


def test_started_update_title_uses_tool_summary(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(call_id="1", name="ListDir", args={"dirname": ""})

    update = tool_call_started_update(event, registry, tmp_path)

    assert update.kind == "search"
    assert update.status == "in_progress"
    assert update.tool_call_id == "1"
    assert "List dir" in update.title


def test_read_file_location_resolves_relative_path_and_sets_line(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(
        call_id="1", name="ReadFile", args={"filename": "sub/foo.txt", "start_line": 5})

    update = tool_call_started_update(event, registry, tmp_path)

    expected_path = str(canonicalize_dir(Path("sub/foo.txt"), tmp_path))
    assert update.locations is not None
    assert len(update.locations) == 1
    assert update.locations[0].path == expected_path
    assert update.locations[0].line == 5


def test_tool_with_no_location_arg_emits_no_locations(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(call_id="1", name="Bash", args={"command": "echo hi"})

    update = tool_call_started_update(event, registry, tmp_path)

    assert update.locations is None


def test_location_arg_present_in_map_but_missing_from_call_emits_no_locations(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallStartedEvent(call_id="1", name="Grep", args={"pattern": "foo"})

    update = tool_call_started_update(event, registry, tmp_path)

    assert update.locations is None


@pytest.mark.parametrize("name", sorted(TOOL_LOCATION_ARG))
def test_every_location_arg_entry_matches_the_tools_real_parameter(name: str, tmp_path: Path) -> None:
    """Guards against `TOOL_LOCATION_ARG` drifting from a tool's actual argument name (a real
    risk here: several of these tools name their path argument `filename`/`dirname`, not
    `path`)."""
    registry = _registry(tmp_path)
    tool = registry.instantiate_tool(name)
    arg_key = TOOL_LOCATION_ARG[name]
    parameters = tool.parameters()

    assert isinstance(parameters, dict)
    assert arg_key in parameters["properties"]


def test_diff_text_reassembles_add_only_hunk_with_no_old_text() -> None:
    hunks = build_diff_hunks([], ["a", "b"])

    old_text, new_text = _diff_text(hunks)

    assert old_text is None
    assert new_text == "a\nb"


def test_diff_text_reassembles_del_only_hunk() -> None:
    hunks = build_diff_hunks(["a", "b"], [])

    old_text, new_text = _diff_text(hunks)

    assert old_text == "a\nb"
    assert new_text == ""


def test_diff_text_reassembles_mixed_hunk_with_context() -> None:
    hunks = build_diff_hunks(["a", "b", "c"], ["a", "B", "c"], context=1)

    old_text, new_text = _diff_text(hunks)

    assert old_text == "a\nb\nc"
    assert new_text == "a\nB\nc"


def test_diff_text_reassembles_multiple_separated_hunks() -> None:
    old_lines = [str(i) for i in range(30)]
    new_lines = list(old_lines)
    new_lines[2] = "X"
    new_lines[27] = "Y"

    hunks = build_diff_hunks(old_lines, new_lines, context=2)

    assert len(hunks) == 2
    old_text, new_text = _diff_text(hunks)
    assert old_text is not None
    assert "X" in new_text
    assert "Y" in new_text
    assert "2" in old_text.split("\n")


def test_finished_update_emits_diff_content_for_a_successful_edit_file_call(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    (tmp_path / "foo.txt").write_text("a\nb\nc\n")
    tool = registry.instantiate_tool("EditFile")
    args = {
        "filename": "foo.txt", "start_line": 2, "end_line": 2,
        "start_text": "b", "end_text": "b", "new_text": "B",
    }
    result = tool.apply(args)
    event = ToolCallEvent(call_id="1", name="EditFile", args=args, result=result, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.status == "completed"
    assert update.content is not None
    assert len(update.content) == 1
    block = update.content[0]
    assert block.type == "diff"
    assert block.path == str(canonicalize_dir(Path("foo.txt"), tmp_path))
    assert "B" in block.new_text
    assert block.field_meta is not None
    assert block.field_meta["klorb"]["diffHunks"]


def test_finished_update_falls_back_to_detail_view_for_a_non_diff_tool(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    (tmp_path / "foo.txt").write_text("a\nb\nc\n")
    tool = registry.instantiate_tool("ReadFile")
    args = {"filename": "foo.txt"}
    result = tool.apply(args)
    event = ToolCallEvent(call_id="1", name="ReadFile", args=args, result=result, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.status == "completed"
    assert update.content is not None
    assert len(update.content) == 1
    block = update.content[0]
    assert block.type == "content"
    assert "foo.txt" in block.content.text


def test_finished_update_reports_failure_text_and_status(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallEvent(
        call_id="1", name="ReadFile", args={"filename": "missing.txt"},
        result=None, error="No such file: missing.txt")

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.status == "failed"
    assert update.content is not None
    block = update.content[0]
    assert block.content.text == "No such file: missing.txt"


def test_finished_update_includes_raw_arguments_for_malformed_json_call(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallEvent(
        call_id="1", name="EditFile", args={}, result=None,
        error="Invalid JSON in tool call arguments for 'EditFile'.",
        raw_arguments='{"filename": "x.py", "new_text": "bad}')

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.status == "failed"
    assert update.content is not None
    text = update.content[0].content.text
    assert "Invalid JSON" in text
    assert '{"filename": "x.py", "new_text": "bad}' in text


def test_finished_update_omits_raw_output_when_not_json_serializable(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallEvent(
        call_id="1", name="ListDir", args={"dirname": ""}, result=object(), error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.raw_output is None


def test_finished_update_keeps_json_serializable_raw_output(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    event = ToolCallEvent(
        call_id="1", name="ListDir", args={"dirname": ""},
        result={"subdirs": [], "files": ["a.txt"], "child_count": 1}, error=None)

    update = tool_call_finished_update(event, registry, tmp_path)

    assert update.raw_output == {"subdirs": [], "files": ["a.txt"], "child_count": 1}


def test_finished_update_handles_no_tool_registry_at_all(tmp_path: Path) -> None:
    event = ToolCallEvent(call_id="1", name="ReadFile", args={"filename": "foo.txt"}, result="ok", error=None)

    update = tool_call_finished_update(event, None, tmp_path)

    assert update.status == "completed"
    assert update.content is not None
