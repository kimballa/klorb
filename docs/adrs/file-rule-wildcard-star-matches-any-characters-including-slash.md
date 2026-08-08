# `readFiles`/`writeFiles` rules support a `*` wildcard matching any characters, including `/`

* Date: 2026-08-07
* Question: `FileAccessTable._matches` only ever compared a canonicalized rule path to a
  canonicalized candidate path with `==`, so a `readFiles`/`writeFiles` entry could name exactly
  one file, never a class of files (`TODO.md`: "Per-file allow/ask/deny is only partially
  implemented — add wildcard/glob support like `*.pem`"). What wildcard grammar should a rule
  string support: a full filesystem glob (`*`, `?`, `[...]`, with `*` stopping at `/` and a
  separate `**` for crossing it, as `klorb.tools.find_file`/`klorb.tools.grep` already do via
  `fnmatch`), or something narrower?
* Answer: Only `*` is a metacharacter, meaning zero or more of any character, including `/` — a
  single `*` already spans directory separators, so there's no separate `**` token. Every other
  character, including `?` and `[...]`, is matched literally. A rule with no `*` in it keeps the
  exact-equality behavior unchanged. `_wildcard_pattern_to_regex` in
  `klorb.permissions.file_access` implements this: split on `*`, `re.escape()` each segment,
  join with `.*`, anchor with `^`/`$`, `fullmatch()` against the candidate's canonicalized path
  string.
* Reasoning: The motivating case (`*.pem`) and every other realistic one (`secrets/*.key`, a
  file named the same across several directories) is "match by name or extension regardless of
  where it lives," which needs `*` to cross `/` to be useful without also requiring a `**`
  entry for every rule. Adding a second wildcard token to mean the same thing `*` already means
  here would just be surface-area with no behavior it enables. This intentionally departs from
  `klorb.permissions.command_access`'s own precedent of a deliberately narrow, single-purpose
  grammar (see
  docs/adrs/command-rule-tokens-support-trailing-star-suffix-wildcards.md) — that module matches
  discrete argv tokens, where "trailing-only" preserves an identity-bearing prefix; a file path
  has no equivalent structure to protect, so the same narrowing argument doesn't carry over.
  `fnmatch` (already used by `find_file`/`grep`) was considered and rejected here because its
  `?`/`[...]` are extra grammar this feature doesn't need, and its `*` already stops at nothing
  in particular (`fnmatch` has no path-separator awareness at all, unlike shell globs) — a
  hand-rolled `*`-only translator is simpler than explaining which parts of `fnmatch`'s grammar
  actually apply, and keeps `?`/`[` available as literal filename characters instead of silently
  swallowing them.
