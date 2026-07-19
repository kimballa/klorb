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
  defaulting to `true`. When true, entries matched by `.gitignore` are reported to the tool
  *separately* from non-ignored ones (see "The shared walk" below) rather than dropped by the
  walk itself, so each tool decides what an ignored entry means for it. When false, the tree is
  walked with no distinction drawn.
* **Reporting hidden entries.** Each tool sets `gitignored_hidden: true` (plus a `note` string
  telling the agent it can re-run with `use_gitignore=false`) only when a gitignored entry it
  *actually cared about* was withheld — a file that matched, not merely a directory that
  happened to be ignored. `FindFile` sets it when a gitignored file would have matched its glob;
  `Grep` sets it when a gitignored file it would otherwise have searched (one passing its
  `file_glob`, if any) was left unsearched. When nothing relevant was hidden, `gitignored_hidden`
  is `false` and there is no `note`. This keeps the flag meaningful: the agent can distinguish
  "nothing matches" from "a match exists but was hidden," and an ignored `node_modules/` that has
  no bearing on the current query never trips it. (Contrast a permission-pruned subtree, which is
  dropped silently with no flag — see [[permissions]] and
  `docs/adrs/prune-non-allow-subdirs-during-recursive-tree-walk.md`.)
* **`Grep` does not speculatively search ignored files.** It never reads a gitignored file to
  check whether it *would* have matched; it only notes that such a file exists (and passes its
  `file_glob`). `gitignored_hidden` means "some file I would have searched went unsearched," and
  the agent decides whether a follow-up unfiltered `Grep` is worth it.
* **`FindFile` matches gitignored names without revealing them.** Because a filename glob can be
  tested without reading the file, `FindFile` *does* test gitignored names against its pattern —
  but a gitignored match is only counted toward `gitignored_hidden`, never added to the returned
  `matches` list. The agent learns a hidden match exists and the exact argument to reveal it, but
  the path stays withheld until it opts in.
* **Single-file `Grep` is never filtered.** When `Grep`'s `path` names one explicit file rather
  than a directory, gitignore filtering does not apply — an explicitly named target is honored
  the way `grep <file>` honors it. `gitignored_hidden` stays `false` for a single-file search.

### The shared walk

`walk_readable_tree(context, dirname, *, use_gitignore=True)` yields one
`(dir_path, subdir_names, file_names, gitignored_file_names)` tuple per directory:

* `file_names` are the files a caller treats as visible results; `gitignored_file_names` are the
  files in the same directory that `.gitignore` excludes. When `use_gitignore` is false,
  `gitignored_file_names` is always empty and every file is in `file_names`.
* `subdir_names` lists only the non-ignored child directories; a gitignored directory's name is
  omitted from its parent's `subdir_names`.

Crucially, the walk **still descends into** a gitignored directory — it just reports every file
inside it under `gitignored_file_names` (and omits the directory from its parent's
`subdir_names`). Descending is what lets a tool answer "does this ignored subtree actually contain
a file I care about?" instead of pessimistically assuming it might; that question is exactly what
`gitignored_hidden` now depends on, and it can't be answered without looking inside. Within an
already-ignored subtree every entry is ignored regardless of any nested `.gitignore` — matching
git's rule that you can't re-include a file whose parent directory is excluded — so the walk stops
consulting `.gitignore` files once inside one.

The decision of whether a gitignored file *matters* lives in each tool, not the walk: the walk
only surfaces the ignored files; `FindFileTool`/`GrepTool` apply their own predicate (glob match /
`file_glob`) and set their own `gitignored_hidden`. See
`docs/adrs/gitignore-walk-surfaces-ignored-files-instead-of-deciding-hidden.md`.

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

* Only `.gitignore` files are read. `.git/info/exclude` and the user's global excludes
  (`core.excludesFile`) are not consulted, since the feature is defined in terms of `.gitignore`
  files a project checks in, not a particular user's git configuration.

## `.git/` is skipped unconditionally, outside this mechanism

Any directory literally named `.git` that the walk encounters below its root is pruned by
`walk_readable_tree` itself, hard-coded and independent of `use_gitignore`, `readDirs`
permissions, or any project `.gitignore` file: it is never listed in a `subdir_names`, never
descended into, and never contributes to `gitignored_file_names` or a `gitignored_hidden` note.
This keeps `gitignored_hidden` meaningful (per the flag's contract above) while still keeping
`FindFile`/`Grep` out of repository internals by default — a `.git/` skip is a structural
decision about what a directory walk is for, not a gitignore-style filtering choice a tool
surfaces or an agent can opt out of.

Only the walk's own root is exempt: a caller that explicitly passes a `.git` directory (or a path
inside one) as `dirname`/`path` searches it normally, the same "explicit target is user intent"
principle already used for permission checks and single-file `Grep`. See
`docs/adrs/hard-code-skip-dot-git-dirs-in-tree-walk.md`.
