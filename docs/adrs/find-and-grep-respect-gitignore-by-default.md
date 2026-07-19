# `FindFile`/`Grep` respect `.gitignore` by default, opting out with `use_gitignore=false`

* Partially superseded by: [[hard-code-skip-dot-git-dirs-in-tree-walk]] — the "two deliberate
  scope limits" paragraph below says the literal `.git/` directory is not special-cased. That is
  no longer true: `walk_readable_tree` now hard-codes a `.git/` skip outside the gitignore
  mechanism entirely. Everything else in this ADR still holds.
* Date: 2026-07-16 01:38
* Question: The recursive search tools (`FindFileTool`, `GrepTool`) walk a whole directory tree
  via `klorb.tools.util.dir_walk.walk_readable_tree`. On a real project that tree is full of
  files no agent wants to see — `node_modules/`, `venv/`, build output, minified bundles — that
  the project already enumerates in its `.gitignore`. Left unfiltered, a single `FindFile *.js`
  or `Grep` drowns the agent in vendored/generated matches and burns context. Should the search
  tools honor `.gitignore`, and if so, how does the agent get at an ignored file on the rare
  occasion it needs one? And how should the ignore rules actually be evaluated?
* Answer: Both tools filter out files and directories matched by the project's `.gitignore`
  rules **by default**. Each takes a boolean `use_gitignore` argument defaulting to `true`; the
  agent passes `use_gitignore=false` to walk the tree unfiltered. When the default filter hides
  at least one entry, the result carries `gitignored_hidden: true` plus a `note` string telling
  the agent it can re-run with `use_gitignore=false` — so the omission is never silent, unlike a
  permission-pruned subtree (see
  `docs/adrs/prune-non-allow-subdirs-during-recursive-tree-walk.md`). The filtering lives once
  in the shared `walk_readable_tree`, which grew a `use_gitignore` parameter and an optional
  `WalkReport` out-param it sets `gitignored_hidden` on; both tools thread these through, so
  `FindFile` and `Grep` behave identically. `Grep` does **not** speculatively search ignored
  files to see whether they *would* have matched — it skips them outright and only reports that
  some were skipped, leaving the decision to re-search to the agent.

  Pattern matching is delegated to the `pathspec` library's `GitIgnoreSpec` (added as a
  first-class runtime dependency; it was already present transitively via `mypy`). The
  multi-file precedence git uses — a nested `.gitignore` overriding the ones above it, including
  `!` re-inclusion — is layered on top in `klorb.tools.util.gitignore.GitignoreFilter`, which
  holds an ordered stack of `(base_dir, GitIgnoreSpec)` and lets the innermost matching rule set
  decide. `.gitignore` files are read from the workspace root down through every directory
  walked, so a walk rooted at a subdirectory still honors an ignore rule declared at the repo
  root.
* Reasoning: Respecting `.gitignore` matches what every git-aware search tool the agent's
  training distribution has seen (`git grep`, ripgrep, `fd`) does, so it's the least-surprising
  default and the one that keeps searches useful on real repos. Making it a per-call argument
  rather than a config setting keeps the escape hatch in the agent's own hands mid-task, where
  the "I actually do need to look inside a generated file this once" decision arises, without a
  round-trip through configuration. Surfacing `gitignored_hidden` + `note` (rather than silently
  filtering) is what makes the default safe: the agent can always tell the difference between
  "nothing matches" and "matches exist but were hidden," and knows the exact argument to flip.

  We reject three alternatives. **Shelling out to `git check-ignore`** would be the most
  faithful to git (it also honors `.git/info/exclude` and the user's global excludes), but it
  requires the `git` binary and a real work tree and does nothing for a bare `.gitignore` in a
  non-git directory — klorb is a general harness, not strictly a git tool, and the requirement
  is phrased around `.gitignore` files, not git tracking. **Hand-rolling a gitignore matcher**
  avoids the dependency but re-implements subtle, well-trodden semantics (anchoring, trailing
  slash, `**`, negation) that `pathspec` already gets right and tests exhaustively. **A global
  config toggle** instead of a per-call argument would force the agent to reconfigure the
  session to reach one ignored file, which is the wrong altitude for a momentary need.

  Two deliberate scope limits: the literal `.git/` directory is **not** special-cased — only
  actual `.gitignore` patterns are honored, so the `gitignored_hidden` flag stays meaningful
  (it would otherwise be true on essentially every call in a git repo). And when `Grep`'s
  `path` names a single explicit file, gitignore filtering does not apply — an explicitly named
  target is honored the way `grep <file>` honors it, matching the "explicit target is user
  intent" principle already established for permission checks.
