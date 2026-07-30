# `commandRules` literal tokens support an embedded `*` glob, not just whole-token wildcards

* Date: 2026-07-30
* Question: The bash risk classifier sometimes proposes a `suggested_pattern` that puts a `*`
  *inside* a token rather than as a whole token of its own -- e.g. `["dd", "if=/dev/zero", "of=*",
  "bs=1", "count=32"]` for a candidate argv of `["dd", "if=/dev/zero",
  "of=/home/aaron/zeros.bin", "bs=1", "count=32"]`. `pattern_matches_argv` only recognized `"*"`
  (`WILDCARD_TOKEN`), `"?"` (`OPTIONAL_TOKEN`), and `"**"` (`UNBOUNDED_TOKEN`) as whole-token
  equality checks against those three exact strings, so `"of=*"` fell through to the plain-literal
  branch and was compared to `"of=/home/aaron/zeros.bin"` with `==` -- never matching, so the
  classifier's own patterns were routinely discarded by
  `risk_classifier._discard_nonmatching_suggested_patterns` as "hallucinated." Should the matcher
  special-case a literal token that merely embeds `*` characters (`--arg=*`, `*.py`), so a
  `--flag=value`/`key=value`-shaped argument can be generalized on just its value half, or should
  a rule author/the classifier be told to keep using a bare `"*"`/`"**"` token there instead
  (accepting that it also matches a wholly different flag in that position)?
* Answer: An embedded `*` inside a literal token (anything that contains `*` but doesn't equal
  exactly `"*"` or `"**"`) is now its own case: a single-token glob. It still occupies exactly one
  argv position -- never zero, never more, exactly like `WILDCARD_TOKEN` -- but the candidate token
  at that position must match the glob (each `*` in the pattern token matches any run of
  characters, including none, within that one candidate token) rather than compare `==` to it.
  `["dd", "if=/dev/zero", "of=*", "bs=1", "count=*"]` now matches the `dd ... of=<path>
  ... count=<n>` shape directly. `command_access._token_matches_literal` implements this: no `*`
  in the token means the old exact-equality literal branch, unchanged; a `*` anywhere else
  translates the token to a regex by escaping the non-`*` segments and joining them with `.*`,
  then requires a full match against the single candidate token.
* Reasoning: A whole-token `"*"`/`"**"` was already the documented way to generalize an *entire*
  argument (a file path, a commit message, one flag on its own), and that's still correct for
  those shapes -- this ADR doesn't change `WILDCARD_TOKEN`/`OPTIONAL_TOKEN`/`UNBOUNDED_TOKEN`
  semantics at all (see
  docs/adrs/command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md
  for those). The gap was specifically a `--flag=value`/`key=value` token, where the flag/key name
  and the `=` are the part worth keeping literal (they're what makes the rest of the pattern
  trustworthy -- an author who wants to allow varying `of=` values should not have to also accept
  an arbitrary *different* flag landing in that slot) and only the value should vary. Before this
  change, the only way to express "same flag name, any value" was to widen the whole token to `*`
  or `**`, which silently also permits a same-arity but differently-named flag/argument in that
  position -- broader than what's actually safe to repeat, the opposite of the "least permissive
  generalization" instruction the classifier is already given. Implementing this as a regex
  translation (rather than, say, `fnmatch`) keeps the semantics to exactly one metacharacter (`*`
  only) that this codebase already treats as special everywhere else in this grammar; `fnmatch`
  would also grant `?`/`[seq]` glob meanings to those characters inside a literal token, which
  would silently reinterpret an author's literal `?` or `[` /`]` (e.g. inside a real shell glob
  argument like `file[1].txt` that was never expanded) as unintended glob metacharacters instead
  of literal text.

  `_has_unsafe_wildcard_argv0` (`klorb.permissions.risk_classifier`) is widened alongside this: an
  argv0 that embeds `*` (`["py*", "test"]`) is unsafe for the same reason a whole-token wildcard
  argv0 already was -- it still means "any program whose name happens to match this," and the
  program *is* what decides what a command does, so generalizing it can never be trusted based on
  the rest of the pattern alone. The one exception remains the whole-token `["*", <version/help
  flag>]` shape; an embedded-glob argv0 gets no equivalent exception.
