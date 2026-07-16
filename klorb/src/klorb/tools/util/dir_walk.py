# © Copyright 2026 Aaron Kimball
"""Shared recursive, permission-aware directory walk used by `GrepTool` and `FindFileTool`.

Both tools need to walk a whole directory tree rather than list a single level (unlike
`klorb.tools.list_dir.ListDirTool`), so the permission-aware resolution logic that decides which
subdirectories a walk may descend into lives here once rather than being duplicated between them.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_read
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.util.gitignore import GitignoreFilter

logger = logging.getLogger(__name__)


@dataclass
class WalkReport:
    """Out-of-band signal a caller can pass into `walk_readable_tree` to learn things about the
    walk that don't fit its `(dir_path, subdir_names, file_names)` yield shape.

    `gitignored_hidden` is set to `True` if the walk pruned at least one file or subdirectory
    because a `.gitignore` rule matched it (only possible when `use_gitignore` is on). A tool
    reads it after the walk to tell the agent that some results were withheld and can be
    surfaced by re-running with `use_gitignore=false`.
    """

    gitignored_hidden: bool = False


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
    context: ToolSetupContext,
    dirname: str,
    *,
    use_gitignore: bool = True,
    report: WalkReport | None = None,
) -> Iterator[tuple[Path, list[str], list[str]]]:
    """Depth-first walk of the directory tree rooted at `dirname`, yielding
    `(dir_path, subdir_names, file_names)` for `dir_path` itself and then every directory
    beneath it that `readDirs` permits. `dir_path` is an absolute, canonicalized `Path`;
    `subdir_names`/`file_names` are bare names (not full paths), each sorted alphabetically.

    When `use_gitignore` is true (the default), files and subdirectories matched by the
    project's `.gitignore` rules are omitted from `file_names`/`subdir_names` and, for a
    directory, not descended into — so a bulk search skips `node_modules/`, build output, and the
    like the way `git` and ripgrep do. `.gitignore` files are read from the workspace root down
    through every directory walked, with a nested file overriding those above it (see
    `klorb.tools.util.gitignore.GitignoreFilter`). Pass `use_gitignore=False` to walk the tree
    unfiltered. If a `report` is supplied, its `gitignored_hidden` flag is set to `True` when at
    least one entry is dropped for this reason, so the caller can tell the agent that hidden
    results exist. See docs/specs/gitignore-aware-tree-walk.md.

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

    gitignore: GitignoreFilter | None = None
    if use_gitignore:
        workspace_root = context.session_config.workspace.path.resolve()
        gitignore = GitignoreFilter.for_root(workspace_root, root_path)

    yield from _walk(context, root_path, gitignore, report)


def _walk(
    context: ToolSetupContext,
    dir_path: Path,
    gitignore: GitignoreFilter | None,
    report: WalkReport | None,
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

    if gitignore is not None:
        kept_files: list[str] = []
        for filename in files:
            if gitignore.is_ignored(dir_path / filename, is_dir=False):
                if report is not None:
                    report.gitignored_hidden = True
            else:
                kept_files.append(filename)
        files = kept_files

    descendable: list[Path] = []
    for sub in subdirs:
        if not _is_descendable(context, sub):
            continue
        if gitignore is not None and gitignore.is_ignored(sub, is_dir=True):
            if report is not None:
                report.gitignored_hidden = True
            continue
        descendable.append(sub)
    yield dir_path, [sub.name for sub in descendable], files

    for sub in descendable:
        child_gitignore = gitignore.descend(sub) if gitignore is not None else None
        yield from _walk(context, sub, child_gitignore, report)
