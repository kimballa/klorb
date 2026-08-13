#!/usr/bin/env python3
# © Copyright 2026 Aaron Kimball
"""onActivateSkill handler for /enable-software-factory. Creates the sentinel file that turns
software-factory mode on, recording the activating session's `root_session_id` as the latch's
owner -- see `queue_utils.read_latch_owner`.
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

    queue_utils.auto_dir(workspace_root).mkdir(parents=True, exist_ok=True)
    queue_utils.write_latch_owner(
        queue_utils.enable_sentinel_path(workspace_root), root_session_id)
    queue_utils.emit_hook_output(success=True)


if __name__ == "__main__":
    main()
