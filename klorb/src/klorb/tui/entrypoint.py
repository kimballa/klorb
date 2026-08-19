# © Copyright 2026 Aaron Kimball
"""`run_repl`: the `cli.py`-facing entry point that launches `ReplApp`, plus its crash
handling."""

import logging
import sys
from pathlib import Path

from klorb.logging_config import CrashLogTee, crash_log_path
from klorb.process_config import ProcessConfig
from klorb.session import Session
from klorb.tui.app import ReplApp
from klorb.workspace import TrustManager

logger = logging.getLogger(__name__)


def _handle_repl_crash(app: ReplApp, crash_tee: CrashLogTee) -> None:
    """Handle a REPL crash by printing the crash log location and saving session state."""
    log_path = crash_tee.opened_log_path()
    if log_path is not None:
        print(f"klorb crashed; full stack trace written to {log_path}", file=sys.stderr)
    else:
        print("klorb crashed; could not write a crash log file.", file=sys.stderr)

    live_session = app._session
    if not live_session.config.workspace.trusted:
        return
    try:
        live_session.persist_state()
    except OSError:
        logger.warning("Could not save session state on crash.", exc_info=True)
        print("klorb crashed; could not save session state.", file=sys.stderr)
    else:
        print("klorb crashed; session state saved.", file=sys.stderr)


def run_repl(
    session: Session | None = None,
    process_config: ProcessConfig | None = None,
    initial_message: str | None = None,
    session_log_enabled: bool = True,
    trust_manager: TrustManager | None = None,
    config_flag_path: Path | None = None,
    skip_session_restore: bool = False,
    quit_on_success: bool = False,
) -> None:
    """Launch the interactive klorb REPL, optionally submitting `initial_message` first."""
    workspace_root = session.config.workspace.path if session is not None else Path.cwd()
    crash_tee = CrashLogTee(sys.stderr, crash_log_path(workspace_root))
    app = ReplApp(
        session=session,
        process_config=process_config,
        initial_message=initial_message,
        session_log_enabled=session_log_enabled,
        trust_manager=trust_manager,
        config_flag_path=config_flag_path,
        skip_session_restore=skip_session_restore,
        quit_on_success=quit_on_success,
    )
    app.error_console.file = crash_tee
    try:
        app.run()
        if app.return_code == 1:
            _handle_repl_crash(app, crash_tee)
    finally:
        # Belt-and-suspenders alongside `ReplApp.on_unmount`: make sure the liveness watchdog
        # can't fire during post-run crash handling / save once the event loop has stopped
        # snoozing it.
        app._watchdog.stop()
        crash_tee.close()
    if app._final_turn_response is not None:
        print(app._final_turn_response)
