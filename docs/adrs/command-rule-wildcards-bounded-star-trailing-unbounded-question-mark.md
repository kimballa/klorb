# `CommandRules` wildcards: `*` is always exactly-one; only a trailing `?` is unbounded

* Date: 2026-07-08 09:00
* Question: A `CommandRules` token pattern's wildcard originally used `*` for two different
  things depending on position: exactly one token in the middle of a pattern, or "any number of
  further tokens, including zero" as the pattern's last token. Is overloading one symbol with two
  different quantities the right design, or does a `CommandRules` author need a way to express
  "optional" and "unbounded" more explicitly and separately?
* Answer: Two symbols, each with one consistent meaning: `WILDCARD_TOKEN` (`"*"`) always matches
  exactly one arbitrary token, at any position — including a pattern's last token, which no
  longer gets special-cased. `OPTIONAL_TOKEN` (`"?"`) matches zero-or-one arbitrary token at any
  position *except* as a pattern's own last token, where it matches any number of further
  tokens, including zero (the old trailing-`*` behavior, moved onto a different symbol). A
  non-last `"?"` requires backtracking to resolve (`CommandPermissionsTable._matches`'s
  `match_from` is a small memoized recursive matcher, not a single linear pass) since whether it
  consumes a token depends on what the rest of the pattern needs next.
* Reasoning: The original single-symbol design meant a bare `["foo", "*"]` silently matched zero
  args, one arg, or an unbounded number of args, all from one rule — a config author writing
  `["git", "status", "*"]` to mean "any flags after `git status`" couldn't distinguish that
  intent from "exactly one flag" without reading the implementation, and every mid-pattern `*`
  had to be mentally special-cased as "well, unless it's last." Splitting the two meanings onto
  separate symbols removes that ambiguity: `*` is always "one specific slot, filled by anything,"
  `?` is always "this slot is optional," and only a rule author who explicitly writes `?` as the
  very last token gets the broader "however many more args" behavior — closer to how `?`/`*`
  read in shell globs and regex quantifiers, where `?` already means "zero or one" and a
  trailing wildcard is a distinct, deliberate choice rather than an accidental default.

  This does mean there is no way to express "an unbounded number of args in the *middle* of a
  pattern" or "at most N args, for N > 1, without also matching more" purely declaratively — a
  pattern's unbounded-tail behavior can only live at the very end. That's an accepted, deliberate
  narrowing (not a gap the design tries to route around): `CommandRules` entries gate what a
  model is allowed to run unsupervised, so a config author being forced to spell out
  `["ls", "?", "?", "?"]` for "up to three optional trailing args" (rather than getting an
  implicit "any number" by default) is the safer failure mode — an author who wants broad
  matching still can ask for it explicitly with a trailing `?`, but nothing produces
  broader-than-intended matching by omission.
