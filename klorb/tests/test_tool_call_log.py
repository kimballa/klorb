# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tool_call_log."""

import logging
from pathlib import Path

import pytest

import klorb.tool_call_log as tool_call_log_module
from klorb.tool_call_log import (
    LOG_TOOL_CALLS_ENV_VAR,
    TOOL_CALLS_LOG_FILENAME,
    log_tool_call,
    tool_call_logging_enabled,
)


@pytest.fixture(autouse=True)
def _reset_io_error_logged_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module-level "have we already logged an IOError" flag before every test in
    this module, so a test that triggers one doesn't leave state behind for a later test."""
    monkeypatch.setattr(tool_call_log_module, "_io_error_already_logged", False)


def test_tool_call_logging_enabled_true_when_config_enabled() -> None:
    assert tool_call_logging_enabled(True) is True


def test_tool_call_logging_enabled_false_when_neither_source_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LOG_TOOL_CALLS_ENV_VAR, raising=False)
    assert tool_call_logging_enabled(False) is False


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE"])
def test_tool_call_logging_enabled_true_for_recognized_env_values(
    value: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOG_TOOL_CALLS_ENV_VAR, value)
    assert tool_call_logging_enabled(False) is True


@pytest.mark.parametrize("value", ["0", "false", "yes", ""])
def test_tool_call_logging_enabled_false_for_unrecognized_env_values(
    value: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(LOG_TOOL_CALLS_ENV_VAR, value)
    assert tool_call_logging_enabled(False) is False


def test_log_tool_call_creates_file_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    log_tool_call("ReadFile", {"filename": "foo.py"}, {"content": "hi"}, None)

    log_path = tmp_path / TOOL_CALLS_LOG_FILENAME
    assert log_path.exists()


def test_log_tool_call_first_entry_has_no_leading_blank_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    log_tool_call("ReadFile", {"filename": "foo.py"}, {"content": "hi"}, None)

    contents = (tmp_path / TOOL_CALLS_LOG_FILENAME).read_text(encoding="utf-8")
    assert contents.startswith("---\n")


def test_log_tool_call_writes_request_and_response_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    log_tool_call("ReadFile", {"filename": "foo.py"}, {"content": "hi"}, None)

    contents = (tmp_path / TOOL_CALLS_LOG_FILENAME).read_text(encoding="utf-8")
    assert "Request:" in contents
    assert '"name": "ReadFile"' in contents
    assert '"filename": "foo.py"' in contents
    assert "Response:" in contents
    assert '"content": "hi"' in contents
    request_index = contents.index("Request:")
    response_index = contents.index("Response:")
    assert request_index < response_index


def test_log_tool_call_reports_error_instead_of_result_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    log_tool_call("ReadFile", {"filename": "missing.py"}, None, "No such file")

    contents = (tmp_path / TOOL_CALLS_LOG_FILENAME).read_text(encoding="utf-8")
    assert '"error": "No such file"' in contents
    assert '"result"' not in contents


def test_log_tool_call_appends_with_blank_line_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    log_tool_call("ReadFile", {"filename": "a.py"}, {"content": "a"}, None)
    log_tool_call("ReadFile", {"filename": "b.py"}, {"content": "b"}, None)

    contents = (tmp_path / TOOL_CALLS_LOG_FILENAME).read_text(encoding="utf-8")
    assert contents.count("---") == 2
    assert "\n\n---\n" in contents
    # No leading blank line before the very first entry.
    assert not contents.startswith("\n")


def test_log_tool_call_does_not_truncate_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    log_path = tmp_path / TOOL_CALLS_LOG_FILENAME
    log_path.write_text("pre-existing content\n", encoding="utf-8")

    log_tool_call("ReadFile", {"filename": "a.py"}, {"content": "a"}, None)

    contents = log_path.read_text(encoding="utf-8")
    assert contents.startswith("pre-existing content\n\n---\n")


def _raise_io_error(self: Path, *args: object, **kwargs: object) -> None:
    raise IOError("disk full")


def test_log_tool_call_swallows_io_error_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "open", _raise_io_error)

    log_tool_call("ReadFile", {"filename": "a.py"}, {"content": "a"}, None)


def test_log_tool_call_reports_first_io_error_via_logger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "open", _raise_io_error)

    with caplog.at_level(logging.ERROR, logger=tool_call_log_module.logger.name):
        log_tool_call("ReadFile", {"filename": "a.py"}, {"content": "a"}, None)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1
    assert "disk full" in error_records[0].getMessage()


def test_log_tool_call_only_reports_the_first_io_error_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "open", _raise_io_error)

    with caplog.at_level(logging.ERROR, logger=tool_call_log_module.logger.name):
        log_tool_call("ReadFile", {"filename": "a.py"}, {"content": "a"}, None)
        log_tool_call("ReadFile", {"filename": "b.py"}, {"content": "b"}, None)
        log_tool_call("ReadFile", {"filename": "c.py"}, {"content": "c"}, None)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(error_records) == 1
