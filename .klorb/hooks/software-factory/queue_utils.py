# © Copyright 2026 Aaron Kimball
"""Shared helpers for the software-factory hook scripts.

Standalone stdlib-only module -- imported by on_turn_end.py and on_file_changed.py, never by
klorb's own application code. Keeps the sentinel filenames and the queue.md task-line parsing
rule in one place instead of duplicated across scripts.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

AUTO_DIR_RELATIVE = "docs/plans/auto"
QUEUE_FILENAME = "queue.md"
ENABLE_SENTINEL_NAME = ".enable_software_factory.tmp"
IN_PROGRESS_SENTINEL_NAME = ".factory_in_progress.tmp"
SENTINEL_NAMES = (ENABLE_SENTINEL_NAME, IN_PROGRESS_SENTINEL_NAME)
CONFIG_RELATIVE_PATH = ".klorb/klorb-config.json"

# A task is a top-level (unindented) "- " or "* " bullet. Headings, prose, HTML comments, and
# blank lines are not tasks, so queue.md can carry an explanatory header with no false positive.
_TASK_LINE_PATTERN = re.compile(r"^[-*] \S")


def auto_dir(workspace_root: Path) -> Path:
    return workspace_root / AUTO_DIR_RELATIVE


def enable_sentinel_path(workspace_root: Path) -> Path:
    return auto_dir(workspace_root) / ENABLE_SENTINEL_NAME


def in_progress_sentinel_path(workspace_root: Path) -> Path:
    return auto_dir(workspace_root) / IN_PROGRESS_SENTINEL_NAME


def _queue_has_task_lines(queue_path: Path) -> bool:
    if not queue_path.is_file():
        return False
    return any(_TASK_LINE_PATTERN.match(line) for line in queue_path.read_text().splitlines())


def _has_other_task_files(directory: Path) -> bool:
    for entry in directory.iterdir():
        if entry.name == QUEUE_FILENAME or entry.name in SENTINEL_NAMES:
            continue
        if entry.is_file() and entry.suffix in (".md", ".txt"):
            return True
    return False


def has_pending_work(directory: Path) -> bool:
    """Whether docs/plans/auto/ has a queue.md task bullet or a standalone task file."""
    if not directory.is_dir():
        return False
    return _queue_has_task_lines(directory / QUEUE_FILENAME) or _has_other_task_files(directory)


def read_hook_input() -> dict:
    return json.loads(sys.stdin.read())


def emit_hook_output(**fields: object) -> None:
    json.dump(fields, sys.stdout)
