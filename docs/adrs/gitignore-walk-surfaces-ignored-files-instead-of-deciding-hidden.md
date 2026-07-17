# The gitignore walk surfaces ignored files; each tool decides what "hidden" means

* **Date:** 2026-07-17
* **Question:** How should the shared recursive walk (`klorb.tools.util.dir_walk`) report
  gitignored entries to `FindFileTool` and `GrepTool`, and who decides whether
  `gitignored_hidden` should be set?

## Answer

The walk no longer decides. `walk_readable_tree` yields a four-element tuple per directory —
`(dir_path, subdir_names, file_names, gitignored_file_names)` — descends into gitignored
directories (rather than pruning them), and reports every file under an ignored directory (or
directly matched by a `.gitignore` rule) in the separate `gitignored_file_names` list. Each tool
then applies its own predicate and sets its own `gitignored_hidden`:

* `FindFileTool` tests gitignored names against its glob and sets the flag only when one would
  have matched — never adding the gitignored path to `matches`.
* `GrepTool` sets the flag only when a gitignored file that would have passed its `file_glob` was
  skipped; it still never reads an ignored file's contents.

The previous `WalkReport` out-parameter (with its single `gitignored_hidden: bool` the walk set
whenever it pruned anything) is removed.

## Reasoning

The old walk set `gitignored_hidden` the moment it declined to descend into *any* gitignored
directory, whether or not that directory held anything the caller was actually searching for. So a
`FindFile` for `*.py` in a repo with a gitignored `logs/` full of `*.log` files reported "matches
were hidden" even though nothing there could ever have matched — a false alarm that trained the
agent to burn a follow-up `use_gitignore=false` call on nothing.

"Was a *relevant* file hidden?" is a tool-specific question — `FindFile` means "matches my glob,"
`Grep` means "would have been searched (passes `file_glob`)" — and neither can be answered without
looking inside the ignored subtree. Making the walk descend into ignored directories and hand back
the ignored filenames lets each tool answer its own question, which is the only way the flag stays
meaningful. `FindFile` explicitly walks ignored trees to test names; `Grep` walks them too but
only to notice a would-be-searched file exists, never to read one (preserving its
skip-don't-peek contract).

The cost is that a walk now lists (never reads) the contents of ignored trees like `node_modules/`
even when `use_gitignore=true`. That was accepted deliberately: it is the price of an accurate
flag, and it is bounded to directory listing, not file I/O. Alternatives considered and rejected:

* **Keep pruning ignored dirs, but flag only "non-empty" ones.** Still wrong — "non-empty" isn't
  "relevant," so it keeps the false alarms for any ignored dir that has files the query doesn't
  care about.
* **Have the walk take each tool's predicate as a callback.** Pushes glob/`file_glob` semantics
  into the shared walk and couples it to its two current callers; surfacing the raw ignored files
  keeps the walk subject-agnostic.
* **Keep the `WalkReport` out-param.** It can only carry one whole-walk boolean, which is exactly
  the too-coarse signal being replaced. A per-directory list has to ride the yield shape instead.

See docs/specs/gitignore-aware-tree-walk.md for the resulting behavior and
docs/adrs/find-and-grep-respect-gitignore-by-default.md for why the tools honor `.gitignore` at
all.
