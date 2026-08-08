# `commandRules` literal tokens support a trailing `*` suffix wildcard, not a general glob

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
  support a `*` anywhere inside a literal token (a general single-token glob, e.g. `*.py`,
  `a*b*c`), or only as a token's own trailing character (a plain string-prefix check, e.g.
  `--arg=*`, `of=*`)?
* Answer: Only a token's own *trailing* `*` carries wildcard meaning -- a suffix-wildcard literal.
  `"of=*"` requires the candidate token to start with the literal `"of="` prefix and accepts
  anything (including nothing) after it, implemented as a plain `str.startswith()` check, not a
  regex or general glob. A `*` occurring anywhere else in a pattern token -- not its last character
  -- is just a literal asterisk to match verbatim: `"a*b"` matches only the literal candidate token
  `"a*b"`, never `"aXb"`. `command_access._token_matches_literal` implements this: no trailing `*`
  (or the whole token is exactly `"*"`/`"**"`, already intercepted earlier as `WILDCARD_TOKEN`/
  `UNBOUNDED_TOKEN`) means the old exact-equality literal branch, unchanged; a genuine trailing `*`
  strips it and checks `candidate.startswith(prefix)`.
* Reasoning: A whole-token `"*"`/`"**"` was already the documented way to generalize an *entire*
  argument (a file path, a commit message, one flag on its own), and that's still correct for
  those shapes -- this ADR doesn't change `WILDCARD_TOKEN`/`OPTIONAL_TOKEN`/`UNBOUNDED_TOKEN`
  semantics at all (see
  docs/adrs/00072-command-rule-wildcards-double-star-unbounded-anywhere-question-mark-always-optional.md
  for those). The gap was specifically a `--flag=value`/`key=value` token, where the flag/key name
  and the `=` are the part worth keeping literal -- they're what makes the rest of the pattern
  trustworthy, since an author who wants to allow varying `of=` values should not also have to
  accept an arbitrary *different* flag landing in that slot -- and only the value after that
  prefix should vary.

  A general glob (wildcard anywhere in the token, not just trailing) was considered and rejected as
  needlessly permissive for what this grammar actually needs to express. Every real motivating
  case -- the `dd` example above, `--output=*`, `key=*` -- is a literal prefix followed by a
  free-form value, i.e. a trailing wildcard; nothing in the classifier's own guidance calls for
  matching a suffix or an interior segment. Restricting to trailing-only also preserves an
  invariant this grammar already relies on elsewhere: the *identity-bearing* part of a token
  (a flag name, a `key=` prefix) must stay literal, and only the *value* half may vary -- the same
  principle `_has_unsafe_wildcard_argv0` already enforces for the program name itself. A general
  glob would let the identity-bearing part vary too by putting the wildcard first or in the middle
  (`"-*"` matching any dash-flag, `"*rf"` matching any flag ending in `rf`), which is exactly the
  shape the classifier is separately instructed never to produce for a destructive flag -- allowing
  it mechanically would only invite the same mistake by construction. Restricting the matcher to a
  trailing-only wildcard also drops the implementation to a plain string-prefix check instead of a
  regex built from escaped segments, removing a whole axis of matcher complexity (multiple
  wildcards per token, metacharacter-escaping correctness) that nothing here actually needs.

  This doesn't fully close every "too permissive" case on its own -- `"-*"` is itself a trailing
  wildcard with almost no literal prefix, and nothing here stops an author (or the classifier)
  from writing one. That remains a matter of prompt guidance and reviewer judgment, the same trust
  boundary `_has_unsafe_wildcard_argv0`'s own exceptions already rely on, not something a purely
  positional rule about *where* `*` may appear can guarantee by itself.

  `_has_unsafe_wildcard_argv0` (`klorb.permissions.risk_classifier`) is widened alongside this: an
  argv0 with a trailing `*` (`["py*", "test"]`) is unsafe for the same reason a whole-token
  wildcard argv0 already was -- it still means "any program whose name starts with this prefix,"
  and the program *is* what decides what a command does, so generalizing it can never be trusted
  based on the rest of the pattern alone. The one exception remains the whole-token `["*",
  <version/help flag>]` shape; a suffix-wildcard argv0 gets no equivalent exception. A `*`
  elsewhere in argv0 (not its last character) is just a literal character there and doesn't trip
  this check, consistent with the matcher treating it the same way.
