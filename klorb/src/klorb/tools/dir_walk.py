# © Copyright 2026 Aaron Kimball
"""Shared recursive, permission-aware directory walk used by `GrepTool` and `FindFileTool`.

Both tools need to walk a whole directory tree rather than list a single level (unlike
`klorb.tools.list_dir.ListDirTool`), so the permission-aware resolution logic that decides which
subdirectories a walk may descend into lives here once rather than being duplicated between them.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_read
from klorb.tools.setup_context import ToolSetupContext

logger = logging.getLogger(__name__)


def _is_descendable(context: ToolSetupContext, subdir: Path) -> bool:
    """Whether `subdir` (an already-listed child directory) may be recursed into: its own
    `resolve_and_evaluate_read()` verdict must be `"allow"`. Anything else — `"deny"`, `"ask"`,
    or a `PermissionError` (e.g. a symlink resolving outside the workspace root) — makes it
    non-descendable, silently, rather than propagating an exception out of the walk.
    """
    try:
        _, verdict = resolve_and_evaluate_read(context, str(subdir))
    except PermissionError:
        return False
    return verdict == "allow"


def walk_readable_tree(
    context: ToolSetupContext, dirname: str,
) -> Iterator[tuple[Path, list[str], list[str]]]:
    """Depth-first walk of the directory tree rooted at `dirname`, yielding
    `(dir_path, subdir_names, file_names)` for `dir_path` itself and then every directory
    beneath it that `readDirs` permits. `dir_path` is an absolute, canonicalized `Path`;
    `subdir_names`/`file_names` are bare names (not full paths), each sorted alphabetically.

    `dirname` is resolved and checked exactly like `ListDirTool`
    (`klorb.permissions.workspace.resolve_and_evaluate_read`), so a denied or ask-required root
    raises `PermissionError`/`PermissionAskRequired` just like a single-directory tool call. Once
    inside the tree, though, each subdirectory encountered is independently re-checked before
    being descended into: one restricted subtree is pruned — excluded from `subdir_names` and
    never yielded itself — rather than aborting the whole walk or raising, since one directory
    somewhere under a large tree being denied or ask-gated shouldn't stop a bulk search from
    covering the rest of it. See
    docs/adrs/prune-non-allow-subdirs-during-recursive-tree-walk.md.

    A subdirectory that is itself a symlink is also excluded from `subdir_names` and never
    descended into, regardless of its permission verdict — mirroring `os.walk`'s
    `followlinks=False` default — so a symlink cycle can't recurse forever. See
    docs/adrs/recursive-tree-walk-does-not-follow-symlinked-dirs.md.
    """
    root_path, verdict = resolve_and_evaluate_read(context, dirname)
    raise_if_not_allowed(
        verdict, resource_description=f"search {root_path}", path=root_path, is_write=False)

    if not root_path.exists():
        raise FileNotFoundError(f"{dirname!r} does not exist")
    if not root_path.is_dir():
        raise NotADirectoryError(f"{dirname!r} is not a directory")

    yield from _walk(context, root_path)


def _walk(
    context: ToolSetupContext, dir_path: Path,
) -> Iterator[tuple[Path, list[str], list[str]]]:
    subdirs: list[Path] = []
    files: list[str] = []
    for entry in dir_path.iterdir():
        if entry.is_symlink():
            if not entry.is_dir():
                files.append(entry.name)
            continue
        if entry.is_dir():
            subdirs.append(entry)
        else:
            files.append(entry.name)
    subdirs.sort(key=lambda p: p.name)
    files.sort()

    descendable = [sub for sub in subdirs if _is_descendable(context, sub)]
    yield dir_path, [sub.name for sub in descendable], files

    for sub in descendable:
        yield from _walk(context, sub)
