# © Copyright 2026 Aaron Kimball
"""Tests for klorb.schema_envelope."""

import json
import logging
from pathlib import Path

import pytest

from klorb.schema_envelope import read_versioned_json, write_versioned_json


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    assert read_versioned_json(tmp_path / "missing.json", expected_schema_name="klorb-config") == {}


def test_reads_data_alongside_matching_schema_block(tmp_path: Path) -> None:
    path = tmp_path / "klorb-config.json"
    _write_json(path, {"schema": {"name": "klorb-config", "version": "1.0.0"}, "model": "some/model"})

    assert read_versioned_json(path, expected_schema_name="klorb-config") == {"model": "some/model"}


def test_accepts_file_with_no_schema_block(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "klorb-config.json"
    _write_json(path, {"model": "some/model"})

    with caplog.at_level(logging.DEBUG, logger="klorb.schema_envelope"):
        result = read_versioned_json(path, expected_schema_name="klorb-config")

    assert result == {"model": "some/model"}
    assert "no schema block" in caplog.text


def test_mismatched_schema_name_is_ignored_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "wrong-type.json"
    _write_json(path, {"schema": {"name": "klorb-session", "version": "1.0.0"}, "model": "some/model"})

    with caplog.at_level(logging.WARNING, logger="klorb.schema_envelope"):
        result = read_versioned_json(path, expected_schema_name="klorb-config")

    assert result == {}
    assert "klorb-session" in caplog.text


def test_malformed_json_is_skipped_and_logged_rather_than_raising(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "klorb-config.json"
    path.write_text('{"model": "some/model",}', encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="klorb.schema_envelope"):
        result = read_versioned_json(path, expected_schema_name="klorb-config")

    assert result == {}
    assert str(path) in caplog.text


def test_malformed_json_warning_includes_line_context_around_the_error(tmp_path: Path) -> None:
    """The `warnings` list collects a message naming the file and a small excerpt of the lines
    around the parse error (see `klorb.schema_envelope._format_json_error_context`), not just
    a bare byte offset — this is what `klorb.tui.ReplApp` posts to the history scroll."""
    path = tmp_path / "klorb-config.json"
    path.write_text('{\n  "model": "some/model",\n  "thinking.effort": "high",\n}\n', encoding="utf-8")

    warnings: list[str] = []
    result = read_versioned_json(path, expected_schema_name="klorb-config", warnings=warnings)

    assert result == {}
    assert len(warnings) == 1
    assert str(path) in warnings[0]
    assert '"thinking.effort": "high",' in warnings[0]
    assert "^" in warnings[0]


def test_no_warnings_collected_when_warnings_arg_omitted(tmp_path: Path) -> None:
    """`warnings` is optional — a caller that doesn't pass it (e.g. `persist_theme`) still gets
    the permissive `{}`-on-parse-failure behavior, just without a collected message."""
    path = tmp_path / "klorb-config.json"
    path.write_text('{"model": "some/model",}', encoding="utf-8")

    assert read_versioned_json(path, expected_schema_name="klorb-config") == {}


def test_write_collapses_permission_rule_list_elements_onto_one_line(tmp_path: Path) -> None:
    """The `allow`/`ask`/`deny` lists get each element rendered on a single line, even nested
    deep under `sessionDefaults.commandRules`, while the rest of the document stays pretty-printed
    with two-space indentation. See docs/specs/process-and-session-config.md's "On-disk key
    naming" section."""
    path = tmp_path / "klorb-config.json"
    write_versioned_json(
        path,
        {
            "sessionDefaults": {
                "readDirs": {"deny": [], "ask": [], "allow": ["/home/a", "/home/b"]},
                "commandRules": {
                    "deny": [],
                    "ask": [],
                    "allow": [["grep", "-i", "*"], ["python", "-m", "pytest", "**"]],
                },
            },
        },
        schema_name="klorb-config",
        schema_version="1.0.0",
    )
    text = path.read_text(encoding="utf-8")

    # Each command pattern is emitted whole on one line, not one token per line...
    assert '["grep", "-i", "*"],' in text
    assert '["python", "-m", "pytest", "**"]' in text
    # ...and each directory-path element likewise sits on its own single line.
    assert '        "/home/a",\n' in text
    # The surrounding document is still indented/pretty-printed, and round-trips cleanly.
    assert '  "sessionDefaults": {' in text
    assert read_versioned_json(path, expected_schema_name="klorb-config") == {
        "sessionDefaults": {
            "readDirs": {"deny": [], "ask": [], "allow": ["/home/a", "/home/b"]},
            "commandRules": {
                "deny": [],
                "ask": [],
                "allow": [["grep", "-i", "*"], ["python", "-m", "pytest", "**"]],
            },
        },
    }
