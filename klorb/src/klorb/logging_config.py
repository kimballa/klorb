# © Copyright 2026 Aaron Kimball
"""Configures klorb's stdlib logging: a TextualHandler plus a per-session log file."""

import logging
from pathlib import Path

from textual.logging import TextualHandler

from klorb.paths import SESSION_LOGS_DIR


def session_log_path(session_id: str) -> Path:
    """Build the session log file path for the given session id: SESSION_LOGS_DIR/<id>.log."""
    return SESSION_LOGS_DIR / f"{session_id}.log"


def configure_logging(*, repl_mode: bool, log_path: Path | None) -> None:
    """Configure the root logger.

    In the REPL (`repl_mode=True`), logs go to a `TextualHandler` (the active Textual
    app's console, or stderr when none is running). Otherwise, for a one-shot prompt,
    logs go directly to stderr. When `log_path` is given, log records are also written to
    that file.
    """
    handlers: list[logging.Handler] = [TextualHandler()] if repl_mode else [logging.StreamHandler()]

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))

    logging.basicConfig(level="NOTSET", handlers=handlers, force=True)
