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
from klorb.workspace.last_session import last_session_path, write_last_session

logger = logging.getLogger(__name__)


def _handle_repl_crash(app: ReplApp, crash_tee: CrashLogTee) -> None:
    """Called by `run_repl()` once `App.run()` returns with `app.return_code == 1` —
    `App._handle_exception` sets that on an unhandled exception, and never raises it back out
    of `run()` itself (Textual prints the traceback and exits the app instead).

    Prints a pointer to the crash log file `crash_tee` captured (see `CrashLogTee`), or a
    fallback line if the file couldn't be opened. Then, if the crashed session's workspace is
    trusted, also saves its state via `klorb.workspace.last_session.write_last_session` — the
    same mechanism `ReplApp._quit_after_maybe_saving` uses for a normal, user-confirmed quit —
    since there's no modal to confirm through once the app has already crashed and the
    conversation would otherwise just be lost. Uses `app._session`, the app's live session at
    crash time, rather than whatever `Session` `run_repl()` was originally called with, since
    `/clear` (and workspace-trust changes) can replace or mutate it over the app's lifetime.
    """
    log_path = crash_tee.opened_log_path()
    if log_path is not None:
        print(f"klorb crashed; full stack trace written to {log_path}", file=sys.stderr)
    else:
        print("klorb crashed; could not write a crash log file.", file=sys.stderr)

    live_session = app._session
    if not live_session.config.workspace.trusted:
        return
    try:
        write_last_session(
            live_session.config.workspace, live_session.config, live_session.messages,
            statistics=live_session.statistics,
            session_id=live_session.id,
            session_name=live_session.name,
            cur_chainlink_task_id=live_session.cur_chainlink_task_id)
    except OSError:
        logger.warning("Could not save session state on crash.", exc_info=True)
        print("klorb crashed; could not save session state.", file=sys.stderr)
    else:
        print(
            f"klorb crashed; session state saved to "
            f"{last_session_path(live_session.config.workspace)}",
            file=sys.stderr)


def run_repl(
    session: Session | None = None,
    process_config: ProcessConfig | None = None,
    initial_message: str | None = None,
    session_log_enabled: bool = True,
    trust_manager: TrustManager | None = None,
    config_flag_path: Path | None = None,
) -> None:
    """Launch the interactive klorb REPL, optionally submitting `initial_message` first.

    On an unhandled exception, Textual prints a full traceback to stderr and exits the app
    rather than raising out of `App.run()` (see `App._handle_exception`), so that traceback is
    also captured to a `/tmp` crash log file — via `CrashLogTee` standing in for `App.
    error_console`'s output stream — before `_handle_repl_crash` reports the crash (and saves
    session state, where possible) once `run()` returns.
    """
    workspace_root = session.config.workspace.path if session is not None else Path.cwd()
    crash_tee = CrashLogTee(sys.stderr, crash_log_path(workspace_root))
    app = ReplApp(
        session=session,
        process_config=process_config,
        initial_message=initial_message,
        session_log_enabled=session_log_enabled,
        trust_manager=trust_manager,
        config_flag_path=config_flag_path,
    )
    app.error_console.file = crash_tee
    try:
        app.run()
        if app.return_code == 1:
            _handle_repl_crash(app, crash_tee)
    finally:
        # Belt-and-suspenders alongside `ReplApp.on_unmount`: make sure the liveness watchdog
        # can't fire during post-run crash handling / save once the event loop has stopped
        # snoozing it (see docs/specs/interrupt-and-liveness-watchdog.md).
        app._watchdog.stop()
        crash_tee.close()
