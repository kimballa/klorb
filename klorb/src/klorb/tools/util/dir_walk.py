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
from klorb.tools.util.gitignore import GitignoreFilter

logger = logging.getLogger(__name__)

GIT_DIR_NAME = ".git"


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
) -> Iterator[tuple[Path, list[str], list[str], list[str]]]:
    """Depth-first walk of the directory tree rooted at `dirname`, yielding
    `(dir_path, subdir_names, file_names, gitignored_file_names)` for `dir_path` itself and then
    every directory beneath it that `readDirs` permits. `dir_path` is an absolute, canonicalized
    `Path`; the three name lists are bare names (not full paths), each sorted alphabetically.

    `file_names` holds the files a caller should treat as visible results; `gitignored_file_names`
    holds the files in the same directory that the project's `.gitignore` rules exclude. When
    `use_gitignore` is true (the default), the walk still **descends into** gitignored
    directories — so a caller can find out whether a gitignored subtree actually contains a file
    it cares about — but every file inside a gitignored directory (and every file directly matched
    by a `.gitignore` rule) is reported under `gitignored_file_names` rather than `file_names`,
    and a gitignored directory's name is omitted from its parent's `subdir_names`. This split lets
    each tool decide for itself what a gitignored entry means: `FindFileTool` tests gitignored
    names against its glob and flags a match without reporting the path, while `GrepTool` skips
    reading gitignored files but flags that some file it would otherwise have searched was left
    unsearched. When `use_gitignore` is false, no filtering happens at all: every file is in
    `file_names` and `gitignored_file_names` is always empty. `.gitignore` files are read from the
    workspace root down through every directory walked, with a nested file overriding those above
    it (see `klorb.tools.util.gitignore.GitignoreFilter`). See
    docs/specs/gitignore-aware-tree-walk.md and
    docs/adrs/gitignore-walk-surfaces-ignored-files-instead-of-deciding-hidden.md.

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

    Every subdirectory named `.git` encountered anywhere below the walk's root is skipped
    unconditionally: it is never listed in `subdir_names`, never yielded, and never counted
    toward `gitignored_file_names` or a "hidden matches" note, regardless of `use_gitignore` or
    `readDirs` permissions. This is a hard-coded exclusion, not a filtering decision either tool
    surfaces to the agent. Only the walk's own root is exempt — a caller that explicitly names a
    `.git` directory (or a path beneath one) as `dirname` searches it normally. See
    docs/adrs/hard-code-skip-dot-git-dirs-in-tree-walk.md.
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

    yield from _walk(context, root_path, gitignore, parent_ignored=False)


def _walk(
    context: ToolSetupContext,
    dir_path: Path,
    gitignore: GitignoreFilter | None,
    *,
    parent_ignored: bool,
) -> Iterator[tuple[Path, list[str], list[str], list[str]]]:
    """Recursion helper for `walk_readable_tree`. `parent_ignored` is `True` once the walk is
    somewhere inside a gitignored directory: everything below an excluded directory is itself
    excluded (git can't re-include a file whose parent directory is excluded), so within such a
    subtree every file is reported as gitignored without consulting `gitignore` again — which is
    also why an ignored subtree is descended with `gitignore=None`.
    """
    subdirs: list[Path] = []
    files: list[str] = []
    for entry in dir_path.iterdir():
        if entry.is_symlink():
            if not entry.is_dir():
                files.append(entry.name)
            continue
        if entry.is_dir():
            if entry.name == GIT_DIR_NAME:
                # Hard-coded, unconditional: a nested .git dir is never listed, descended, or
                # counted as a gitignore-hidden entry. Only the walk's own root (handled in
                # `walk_readable_tree`, never reaching this loop) is exempt.
                continue
            subdirs.append(entry)
        else:
            files.append(entry.name)
    subdirs.sort(key=lambda p: p.name)
    files.sort()

    kept_files: list[str] = []
    ignored_files: list[str] = []
    for filename in files:
        if parent_ignored or (
                gitignore is not None and gitignore.is_ignored(dir_path / filename, is_dir=False)):
            ignored_files.append(filename)
        else:
            kept_files.append(filename)

    kept_subdirs: list[Path] = []
    ignored_subdirs: list[Path] = []
    for sub in subdirs:
        if not _is_descendable(context, sub):
            continue
        if parent_ignored or (gitignore is not None and gitignore.is_ignored(sub, is_dir=True)):
            ignored_subdirs.append(sub)
        else:
            kept_subdirs.append(sub)

    yield dir_path, [sub.name for sub in kept_subdirs], kept_files, ignored_files

    for sub in kept_subdirs:
        child_gitignore = gitignore.descend(sub) if gitignore is not None else None
        yield from _walk(context, sub, child_gitignore, parent_ignored=False)
    for sub in ignored_subdirs:
        # An ignored subtree needs no further `.gitignore` consulting (nothing under an excluded
        # directory can be re-included), so descend it with `gitignore=None` and parent_ignored.
        yield from _walk(context, sub, None, parent_ignored=True)
