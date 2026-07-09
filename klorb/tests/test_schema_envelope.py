# © Copyright 2026 Aaron Kimball
"""Tests for klorb.schema_envelope."""

import json
import logging
from pathlib import Path

import pytest

from klorb.schema_envelope import read_versioned_json


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
