# © Copyright 2026 Aaron Kimball
"""Runtime diagnostics helpers, kept UI-agnostic so both the CLI/TUI and any other embedder can
use them. Today: an all-thread stack dump written when klorb force-exits from a hang, so a later
investigation can see exactly where every thread — especially a wedged worker thread — was
parked. See docs/specs/interrupt-and-liveness-watchdog.md.
"""

import faulthandler
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path

_HANG_DUMP_PREFIX = "klorb-hang-"


def thread_dump_path(workspace_root: Path) -> Path:
    """Build the path an all-thread stack dump for `workspace_root` is written to:
    `<tempdir>/klorb-hang-<workspace basename>-<timestamp>.log`, timestamped to the second at
    call time so repeated dumps in the same workspace don't collide — mirroring
    `klorb.logging_config.crash_log_path`'s naming for the (distinct) Textual crash case."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    basename = workspace_root.name or "workspace"
    return Path(tempfile.gettempdir()) / f"{_HANG_DUMP_PREFIX}{basename}-{timestamp}.log"


def dump_all_thread_stacks(path: Path) -> Path | None:
    """Write a stack trace for every live thread to `path` and return it, or `None` if the file
    couldn't be written at all.

    Uses `faulthandler.dump_traceback(all_threads=True)` — which walks live threads at the C
    level and so works even when the interpreter is otherwise wedged — as the primary dump, and
    also appends a `traceback`-rendered copy built from `sys._current_frames()` for the readable
    thread-name labels `faulthandler` omits. Best-effort throughout: this only ever runs on the
    way out of a hung process (see `klorb.watchdog.force_exit`), so any failure here must be
    swallowed rather than raised — losing the dump is strictly better than blocking the exit.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"klorb thread dump at {datetime.now().isoformat()}\n")
            handle.write(f"{threading.active_count()} live thread(s)\n\n")
            # Human-readable, thread-name-labelled section first. sys._current_frames() is an
            # interpreter internal, but it's the only way to pair a live frame with its thread's
            # readable name (faulthandler labels threads by numeric id only).
            frames = sys._current_frames()
            names = {thread.ident: thread.name for thread in threading.enumerate()}
            for ident, frame in frames.items():
                label = names.get(ident, "<unknown>")
                handle.write(f"--- Thread {label} (id={ident}) ---\n")
                handle.write("".join(traceback.format_stack(frame)))
                handle.write("\n")
            handle.flush()
            # C-level faulthandler dump appended, robust even if the above missed a thread.
            handle.write("--- faulthandler.dump_traceback(all_threads=True) ---\n")
            handle.flush()
            faulthandler.dump_traceback(file=handle, all_threads=True)
        return path
    except Exception:
        return None
