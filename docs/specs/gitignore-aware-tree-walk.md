# Gitignore-aware recursive tree walk

## Summary

The recursive search tools `FindFile` (`klorb.tools.find_file.FindFileTool`) and `Grep`
(`klorb.tools.grep.GrepTool`) skip files and directories a project's `.gitignore` rules exclude,
so an agent searching a real repository isn't buried in `node_modules/`, `venv/`, build output,
or other vendored/generated content it almost never wants. This is on by default; the agent opts
out per call with `use_gitignore=false`. Both tools share the mechanism, which lives in the
common tree walk `klorb.tools.util.dir_walk.walk_readable_tree` — see [[tool-framework]] for how
these tools are registered and invoked, and
[the ADR](../adrs/find-and-grep-respect-gitignore-by-default.md) for why it works this way and
which alternatives were rejected.

## How it works

* **The `use_gitignore` argument.** Both tools accept an optional boolean `use_gitignore`,
  defaulting to `true`. When true, entries matched by `.gitignore` are omitted from results and
  ignored directories are not descended into. When false, the tree is walked unfiltered.
* **Reporting hidden entries.** When the default filter drops at least one entry, the tool's
  result carries `gitignored_hidden: true` and a `note` string telling the agent it can re-run
  with `use_gitignore=false` to include gitignored files. When nothing was hidden,
  `gitignored_hidden` is `false` and there is no `note`. This makes the omission explicit —
  the agent can always distinguish "nothing matches" from "matches exist but were hidden," and
  it's told the exact argument to flip. (Contrast a permission-pruned subtree, which is dropped
  silently with no flag — see [[permissions]] and
  `docs/adrs/prune-non-allow-subdirs-during-recursive-tree-walk.md`.)
* **`Grep` does not speculatively search ignored files.** It skips them outright; it does not
  read an ignored file to check whether it *would* have matched. `gitignored_hidden` means "some
  files were skipped without being searched," and the agent decides whether a follow-up
  unfiltered `Grep` is worth it.
* **Single-file `Grep` is never filtered.** When `Grep`'s `path` names one explicit file rather
  than a directory, gitignore filtering does not apply — an explicitly named target is honored
  the way `grep <file>` honors it. `gitignored_hidden` stays `false` for a single-file search.

### The shared walk

`walk_readable_tree(context, dirname, *, use_gitignore=True, report=None)` grew two keyword
parameters:

* `use_gitignore` toggles the filtering described above.
* `report: WalkReport | None` is an optional out-param. `WalkReport`
  (`klorb.tools.util.dir_walk.WalkReport`) is a small dataclass with a `gitignored_hidden: bool`
  field the walk sets to `True` when it prunes any entry for a gitignore match. A tool passes a
  fresh `WalkReport()` in and reads the flag back after the walk. It's an out-param rather than
  part of the yielded tuple so the walk's `(dir_path, subdir_names, file_names)` yield shape —
  and every existing caller and test that unpacks it — stays unchanged.

Inside the walk, a gitignored file is removed from the yielded `file_names`, and a gitignored
subdirectory is removed from `subdir_names` **and** never recursed into (its whole subtree is
pruned). Because an ignored directory is never descended, a `!` negation inside a `.gitignore`
buried under an already-excluded directory can't re-include anything — which matches git's rule
that you can't re-include a file whose parent directory is excluded.

### Evaluating `.gitignore` rules

Pattern matching is delegated to the `pathspec` library's `GitIgnoreSpec`, a first-class runtime
dependency (declared in `pyproject.toml`; it was already present transitively via `mypy`).
`klorb.tools.util.gitignore.GitignoreFilter` layers the multi-file precedence git uses on top:

* A `GitignoreFilter` holds an ordered, immutable stack of `(base_dir, GitIgnoreSpec)` — one
  entry per `.gitignore` file, outermost (nearest the workspace root) first.
* `GitignoreFilter.for_root(workspace_root, start_dir)` builds the filter active when a walk
  begins, reading every `.gitignore` from the workspace root down to and including the walk's
  root. Ancestor files are included because git applies a repo-root `.gitignore` to nested
  directories — a walk rooted at a subdirectory must still honor them. (If `start_dir` is not
  inside `workspace_root`, only `start_dir`'s own `.gitignore` is consulted.)
* As the walk descends, `GitignoreFilter.descend(subdir)` returns a new filter with `subdir`'s
  own `.gitignore` (if any) appended, without mutating the parent's filter.
* `GitignoreFilter.is_ignored(path, *, is_dir)` evaluates `path` against each rule set in order
  and lets the *innermost* one that matches decide (`pathspec`'s `check_file(...).include` is
  `True` for an ignore match, `False` for a `!` negation, `None` for no match; the last non-`None`
  result wins). A directory is presented to `pathspec` with a trailing slash so directory-only
  patterns (`build/`) can fire.

### Scope limits

* The literal `.git/` directory is **not** special-cased — only actual `.gitignore` patterns are
  honored. This keeps `gitignored_hidden` meaningful; special-casing `.git/` would make the flag
  true on essentially every call in a git repo. (`.git/` is already pruned in practice on a
  trusted workspace via the same permission machinery that prunes `${workspace_root}/.klorb/` —
  unrelated to this feature.)
* Only `.gitignore` files are read. `.git/info/exclude` and the user's global excludes
  (`core.excludesFile`) are not consulted, since the feature is defined in terms of `.gitignore`
  files a project checks in, not a particular user's git configuration.
