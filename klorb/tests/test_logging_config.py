# © Copyright 2026 Aaron Kimball
"""Tests for klorb.logging_config."""

import logging
import re

from textual.logging import TextualHandler

from klorb import logging_config

SESSION_LOG_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-[a-z]+-[a-z]+\.log$")


def test_configure_logging_creates_session_log_file_when_enabled() -> None:
    log_path = logging_config.configure_logging(repl_mode=True, session_log=True)

    assert log_path is not None
    assert log_path.parent == logging_config.SESSION_LOGS_DIR
    assert log_path.exists()
    assert SESSION_LOG_FILENAME_RE.match(log_path.name)


def test_configure_logging_skips_session_log_file_when_disabled() -> None:
    log_path = logging_config.configure_logging(repl_mode=False, session_log=False)

    assert log_path is None
    assert not any(isinstance(handler, logging.FileHandler) for handler in logging.getLogger().handlers)


def test_configure_logging_attaches_textual_handler_in_repl_mode() -> None:
    logging_config.configure_logging(repl_mode=True, session_log=False)

    handlers = logging.getLogger().handlers
    assert any(isinstance(handler, TextualHandler) for handler in handlers)


def test_configure_logging_attaches_stream_handler_outside_repl_mode() -> None:
    logging_config.configure_logging(repl_mode=False, session_log=False)

    handlers = logging.getLogger().handlers
    assert not any(isinstance(handler, TextualHandler) for handler in handlers)
    assert any(type(handler) is logging.StreamHandler for handler in handlers)


def test_configure_logging_writes_log_records_to_file() -> None:
    log_path = logging_config.configure_logging(repl_mode=True, session_log=True)

    assert log_path is not None
    logging.getLogger("klorb.test_logging_config").info("hello from a test")

    assert "hello from a test" in log_path.read_text(encoding="utf-8")


def test_configure_logging_generates_unique_nonces() -> None:
    # Multiple calls within the same minute must not collide on filename.
    first = logging_config.configure_logging(repl_mode=True, session_log=True)
    second = logging_config.configure_logging(repl_mode=True, session_log=True)

    assert first != second
