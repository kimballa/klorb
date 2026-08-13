#!/usr/bin/env python3
# © Copyright 2026 Aaron Kimball
"""FileSystemModified handler for software-factory mode, watching docs/plans/auto/.

Reads EventInput JSON on stdin, prints HookOutput JSON on stdout. No-ops whenever the enable
sentinel is absent or owned by a different session (see `queue_utils.read_latch_owner`), the
agent is mid-turn, or a task is already in progress.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import queue_utils  # noqa: E402


def main() -> None:
    event_input = queue_utils.read_hook_input()
    workspace_root = Path(event_input["workspace_root"])

    owner = queue_utils.read_latch_owner(queue_utils.enable_sentinel_path(workspace_root))
    if owner is None or owner != event_input.get("root_session_id"):
        queue_utils.emit_hook_output(success=True)
        return
    if event_input.get("is_agent_active"):
        queue_utils.emit_hook_output(success=True)
        return
    if queue_utils.in_progress_sentinel_path(workspace_root).is_file():
        # Don't interrupt a running task with "new work is available" chatter.
        queue_utils.emit_hook_output(success=True)
        return

    # The watcher also sees the sentinel files' own create/delete events -- exclude them so
    # enabling the mode is never mistaken for a new task file appearing.
    updates = [
        update
        for update in event_input.get("fs_updates") or []
        if Path(update["path"]).name not in queue_utils.SENTINEL_NAMES
    ]

    auto_dir = queue_utils.auto_dir(workspace_root)
    queue_touched = any(
        Path(update["path"]).name == queue_utils.QUEUE_FILENAME for update in updates
    )
    if queue_touched and queue_utils.has_pending_work(auto_dir):
        queue_utils.emit_hook_output(
            success=True,
            message="Software factory: new items were added to docs/plans/auto/queue.md.",
        )
        return

    created_files = [
        Path(update["path"]).name
        for update in updates
        if update.get("event") == "created"
        and Path(update["path"]).name != queue_utils.QUEUE_FILENAME
    ]
    if created_files:
        queue_utils.emit_hook_output(
            success=True,
            message=(
                "Software factory: new task file(s) added to docs/plans/auto/: "
                + ", ".join(created_files)
            ),
        )
        return

    queue_utils.emit_hook_output(success=True)


if __name__ == "__main__":
    main()
