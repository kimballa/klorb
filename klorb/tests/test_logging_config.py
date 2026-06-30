# © Copyright 2026 Aaron Kimball
"""Tests for klorb.logging_config."""

import logging
import re

from textual.logging import TextualHandler

from klorb import logging_config

SESSION_LOG_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-[a-z]+-[a-z]+\.log$")


def test_configure_logging_creates_session_log_file() -> None:
    log_path = logging_config.configure_logging()

    assert log_path.parent == logging_config.SESSION_LOGS_DIR
    assert log_path.exists()
    assert SESSION_LOG_FILENAME_RE.match(log_path.name)


def test_configure_logging_attaches_textual_and_file_handlers() -> None:
    logging_config.configure_logging()

    handlers = logging.getLogger().handlers
    assert any(isinstance(handler, TextualHandler) for handler in handlers)
    assert any(isinstance(handler, logging.FileHandler) for handler in handlers)


def test_configure_logging_writes_log_records_to_file() -> None:
    log_path = logging_config.configure_logging()

    logging.getLogger("klorb.test_logging_config").info("hello from a test")

    assert "hello from a test" in log_path.read_text(encoding="utf-8")


def test_configure_logging_generates_unique_nonces() -> None:
    # Multiple calls within the same minute must not collide on filename.
    first = logging_config.configure_logging()
    second = logging_config.configure_logging()

    assert first != second
