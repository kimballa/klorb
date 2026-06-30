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


def configure_logging(*, repl_mode: bool, session_log: bool) -> Path | None:
    """Configure the root logger.

    In the REPL (`repl_mode=True`), logs go to a `TextualHandler` (the active Textual
    app's console, or stderr when none is running). Otherwise, for a one-shot prompt,
    logs go directly to stderr. In either mode, a per-session log file is also created
    when `session_log` is True.

    Returns the path of the session log file that was created, or None if `session_log`
    was False.
    """
    handlers: list[logging.Handler] = [TextualHandler()] if repl_mode else [logging.StreamHandler()]

    log_path: Path | None = None
    if session_log:
        log_path = _build_session_log_path()
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(level="NOTSET", handlers=handlers, force=True)
    return log_path
