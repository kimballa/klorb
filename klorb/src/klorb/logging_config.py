# © Copyright 2026 Aaron Kimball
"""Configures klorb's stdlib logging: a TextualHandler plus a per-session log file."""

import logging
from datetime import datetime
from pathlib import Path

from coolname import generate_slug
from textual.logging import TextualHandler

from klorb.paths import SESSION_LOGS_DIR

# Two-word kebab-case nonce (e.g. "dastardly-happy") to disambiguate log files
# created within the same minute.
NONCE_WORD_COUNT = 2

SESSION_LOG_TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M"


def _build_session_log_path() -> Path:
    """Build a session log file path under SESSION_LOGS_DIR: yyyy-mm-dd-hh-mm-<nonce>.log."""
    SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime(SESSION_LOG_TIMESTAMP_FORMAT)
    nonce = generate_slug(NONCE_WORD_COUNT)
    return SESSION_LOGS_DIR / f"{timestamp}-{nonce}.log"


def configure_logging() -> Path:
    """Configure the root logger with a TextualHandler and a per-session log file.

    Returns the path of the session log file that was created.
    """
    log_path = _build_session_log_path()
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    logging.basicConfig(
        level="NOTSET",
        handlers=[TextualHandler(), file_handler],
        force=True,
    )
    return log_path
