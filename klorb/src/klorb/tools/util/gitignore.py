# © Copyright 2026 Aaron Kimball
"""Gitignore-aware filtering for the shared recursive tree walk (`dir_walk.py`).

`FindFile` and `Grep` both skip files a project's `.gitignore` rules would hide (unless the
caller opts out with `use_gitignore=false`), so an agent searching a repo isn't buried in
`node_modules/`, build output, or virtualenv contents it almost never wants. The pattern
matching itself is delegated to `pathspec`'s `GitIgnoreSpec`; this module layers on the
*multi-file* precedence git actually uses — a `.gitignore` deeper in the tree overrides the
rules of one nearer the root — which a single `GitIgnoreSpec` doesn't model on its own.

See docs/specs/gitignore-aware-tree-walk.md and
docs/adrs/find-and-grep-respect-gitignore-by-default.md.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from pathspec import GitIgnoreSpec

logger = logging.getLogger(__name__)

GITIGNORE_FILENAME = ".gitignore"


@dataclass(frozen=True)
class _ScopedSpec:
    """One `.gitignore` file's compiled rules together with the absolute directory those rules
    are relative to (patterns in a `.gitignore` are anchored to the directory that contains it).
    """

    base: Path
    spec: GitIgnoreSpec


def _load_dir_spec(dir_path: Path) -> _ScopedSpec | None:
    """Read `dir_path/.gitignore` and compile it, or return `None` if there is no readable
    `.gitignore` there (a missing file, a directory by that name, or an undecodable one)."""
    gitignore_path = dir_path / GITIGNORE_FILENAME
    try:
        lines = gitignore_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return _ScopedSpec(base=dir_path, spec=GitIgnoreSpec.from_lines(lines))


class GitignoreFilter:
    """An immutable, ordered stack of `.gitignore` rule sets active at one point in a tree walk.

    Rule sets are ordered outermost-first (nearest the workspace root) to innermost-last
    (the directory currently being walked). `is_ignored` evaluates a path against each in turn
    and lets the *innermost* rule set that matches decide, mirroring git's rule that a nested
    `.gitignore` overrides the directories above it — including re-inclusion via a `!` negation.

    Instances are never mutated: `descend` returns a new filter with one more rule set appended,
    so a walk can hand each subdirectory its own filter without disturbing its parent's.
    """

    def __init__(self, scoped: tuple[_ScopedSpec, ...]) -> None:
        self._scoped = scoped

    @classmethod
    def for_root(cls, workspace_root: Path, start_dir: Path) -> "GitignoreFilter":
        """Build the filter active when a walk begins at `start_dir`, by reading every
        `.gitignore` from `workspace_root` down to and including `start_dir`.

        Ancestor `.gitignore` files matter because git applies them to nested directories: a
        walk rooted at a subdirectory must still honor an ignore rule declared at the repo root.
        If `start_dir` is not inside `workspace_root` (an unusual absolute-path search), only
        `start_dir`'s own `.gitignore` is consulted.
        """
        chain: list[Path] = []
        try:
            relative = start_dir.relative_to(workspace_root)
        except ValueError:
            chain = [start_dir]
        else:
            current = workspace_root
            chain.append(current)
            for part in relative.parts:
                current = current / part
                chain.append(current)
        scoped = tuple(
            spec for spec in (_load_dir_spec(directory) for directory in chain) if spec is not None)
        return cls(scoped)

    def descend(self, subdir: Path) -> "GitignoreFilter":
        """Return the filter active inside `subdir`: this filter plus `subdir`'s own
        `.gitignore` if it has one (otherwise `self`, unchanged)."""
        scoped = _load_dir_spec(subdir)
        if scoped is None:
            return self
        return GitignoreFilter(self._scoped + (scoped,))

    def is_ignored(self, path: Path, *, is_dir: bool) -> bool:
        """Whether `path` (absolute) is excluded by the active `.gitignore` rules. `is_dir`
        must say whether `path` is a directory, since a trailing-slash pattern (`build/`)
        matches directories only — the path is presented to `pathspec` with a trailing slash so
        such patterns can fire."""
        suffix = "/" if is_dir else ""
        ignored = False
        for scoped in self._scoped:
            try:
                relative = path.relative_to(scoped.base)
            except ValueError:
                # `path` sits above this rule set's directory; that rule set can't apply to it.
                continue
            result = scoped.spec.check_file(relative.as_posix() + suffix)
            if result.include is not None:
                # `include` is True when a pattern matches (ignored) and False when a `!`
                # negation matches (re-included); a nearer-the-leaf rule set overrides.
                ignored = result.include
        return ignored
