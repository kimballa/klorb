#!/usr/bin/env python3
# © Copyright 2026 Aaron Kimball
"""onSessionEnd handler that removes both software-factory sentinel files, but only if the
ending session's own `root_session_id` is the one recorded as the latch's owner.

See docs/specs/software-factory.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import queue_utils  # noqa: E402


def main() -> None:
    hook_input = queue_utils.read_hook_input()
    workspace_root = Path(hook_input["workspace_root"])
    root_session_id = hook_input["root_session_id"]

    enable_path = queue_utils.enable_sentinel_path(workspace_root)
    if queue_utils.read_latch_owner(enable_path) == root_session_id:
        enable_path.unlink(missing_ok=True)
        queue_utils.in_progress_sentinel_path(workspace_root).unlink(missing_ok=True)
    queue_utils.emit_hook_output(success=True)


if __name__ == "__main__":
    main()
