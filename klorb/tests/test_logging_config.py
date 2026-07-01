# © Copyright 2026 Aaron Kimball
"""Tests for klorb.logging_config."""

import logging

from textual.logging import TextualHandler

from klorb import logging_config


def test_session_log_path_builds_path_under_session_logs_dir() -> None:
    log_path = logging_config.session_log_path("2026-06-30-10-00-happy-otter")

    assert log_path == logging_config.SESSION_LOGS_DIR / "2026-06-30-10-00-happy-otter.log"


def test_configure_logging_creates_session_log_file_when_given_a_path() -> None:
    log_path = logging_config.session_log_path("some-session-id")
    logging_config.configure_logging(repl_mode=True, log_path=log_path)

    assert log_path.exists()
    assert any(isinstance(handler, logging.FileHandler) for handler in logging.getLogger().handlers)


def test_configure_logging_skips_session_log_file_when_no_path_given() -> None:
    logging_config.configure_logging(repl_mode=False, log_path=None)

    assert not any(isinstance(handler, logging.FileHandler) for handler in logging.getLogger().handlers)


def test_configure_logging_attaches_textual_handler_in_repl_mode() -> None:
    logging_config.configure_logging(repl_mode=True, log_path=None)

    handlers = logging.getLogger().handlers
    assert any(isinstance(handler, TextualHandler) for handler in handlers)


def test_configure_logging_attaches_stream_handler_outside_repl_mode() -> None:
    logging_config.configure_logging(repl_mode=False, log_path=None)

    handlers = logging.getLogger().handlers
    assert not any(isinstance(handler, TextualHandler) for handler in handlers)
    assert any(type(handler) is logging.StreamHandler for handler in handlers)


def test_configure_logging_writes_log_records_to_file() -> None:
    log_path = logging_config.session_log_path("another-session-id")
    logging_config.configure_logging(repl_mode=True, log_path=log_path)

    logging.getLogger("klorb.test_logging_config").info("hello from a test")

    assert "hello from a test" in log_path.read_text(encoding="utf-8")
