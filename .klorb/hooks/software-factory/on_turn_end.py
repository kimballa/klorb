#!/usr/bin/env python3
# © Copyright 2026 Aaron Kimball
"""onAgentTurnEnd handler for software-factory mode.

Reads HookInput JSON on stdin, prints HookOutput JSON on stdout. No-ops whenever the enable
sentinel (docs/plans/auto/.enable_software_factory.tmp) is absent.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import queue_utils  # noqa: E402

_CONTINUE_MESSAGE = (
    "Software factory: your in-progress task still has uncommitted changes. Continue where "
    "you left off."
)
_NEXT_TASK_MESSAGE = (
    "Software factory: the working copy is clean. Run /enable-software-factory to find and "
    "build the next task."
)
_NEXT_TASK_COMMIT_CONFIG_MESSAGE = (
    "Software factory: the working copy contains changes to the klorb config; commit those. "
    "Then run /enable-software-factory to find and build the next task."
)


def _is_tree_clean(workspace_root: Path) -> bool:
    """
    Return true if the git working copy is *mostly* clean - permit changes to the config file,
    but nothing else.
    """
    result = subprocess.run(
        ["git", "-C", str(workspace_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty_paths = map(lambda line: line[3:], filter(None, result.stdout.splitlines()))
    # A mid-loop command approval writes to klorb-config.json; that alone must not read as
    # leftover work and abort the loop.
    return all(path == queue_utils.CONFIG_RELATIVE_PATH for path in dirty_paths)


def _is_config_updated(workspace_root: Path) -> bool:
    """
    Return true if the klorb config file has been updated.
    """
    result = subprocess.run(
        ["git", "-C", str(workspace_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty_paths = list(map(lambda line: line[3:], filter(None, result.stdout.splitlines())))
    # A mid-loop command approval writes to klorb-config.json; that alone must not read as
    # leftover work and abort the loop.
    return all(path == queue_utils.CONFIG_RELATIVE_PATH for path in dirty_paths) \
        and len(dirty_paths) == 1


def main() -> None:
    hook_input = queue_utils.read_hook_input()
    workspace_root = Path(hook_input["workspace_root"])

    if not queue_utils.enable_sentinel_path(workspace_root).is_file():
        queue_utils.emit_hook_output(success=True)
        return

    if not _is_tree_clean(workspace_root):
        # Dirty tree: only nudge if the dirt is the factory's own in-progress task. Stray
        # uncommitted work a human left before enabling the mode is never ours to resume.
        if queue_utils.in_progress_sentinel_path(workspace_root).is_file():
            queue_utils.emit_hook_output(success=True, message=_CONTINUE_MESSAGE)
        else:
            print("Tree is not clean and patch is not in progress; terminating loop.",
                  file=sys.stderr)
            queue_utils.emit_hook_output(success=True)
        return

    if queue_utils.has_pending_work(queue_utils.auto_dir(workspace_root)):
        if _is_config_updated(workspace_root):
            queue_utils.emit_hook_output(success=True, reset_session=True,
                                         message=_NEXT_TASK_COMMIT_CONFIG_MESSAGE)
        else:
            queue_utils.emit_hook_output(success=True, reset_session=True,
                                         message=_NEXT_TASK_MESSAGE)
    else:
        print("No automatable work remains; terminating loop.",
              file=sys.stderr)
        queue_utils.emit_hook_output(success=True)


if __name__ == "__main__":
    main()
