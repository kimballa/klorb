# `CommandRules` wildcards: `**` is unbounded-anywhere; `?` is always exactly zero-or-one

* Date: 2026-07-08 14:00
* Question: `CommandPermissionsTable._matches` already resolves `OPTIONAL_TOKEN` (`"?"`) with a
  small memoized backtracking matcher, because whether a non-last `"?"` consumes a token depends
  on what the rest of the pattern needs next. Given that backtracking machinery already exists,
  does an unbounded "any number of tokens, including zero" match have to stay restricted to a
  pattern's trailing token, or can it be generalized to any position (`["git", "**", "status"]`)
  — and if so, does `"?"` still need its own special "unbounded when I'm last" case?
* Answer: Two symbols, each with one consistent meaning regardless of position. `WILDCARD_TOKEN`
  (`"*"`) still matches exactly one arbitrary token, anywhere, including a pattern's last token.
  `OPTIONAL_TOKEN` (`"?"`) now uniformly matches zero-or-one arbitrary token, anywhere, including
  a pattern's last token — the old "unbounded if I'm last" special case is gone. A new
  `UNBOUNDED_TOKEN` (`"**"`) matches any number of arbitrary tokens, including zero, at *any*
  position in a pattern, not just the end: `["git", "**", "status", "**"]` matches
  `["git", "-C", "dir", "status", "-s"]`. Resolving a `"**"` (like a non-last `"?"`) requires
  backtracking: `CommandPermissionsTable._matches`'s `match_from` tries first "consume nothing
  here, advance the rule" and then, if that fails, "consume one candidate token and stay on this
  rule token" — the same two-way branch `"?"` already used, except the "consume one" branch
  doesn't advance past the `"**"` itself, so it can keep consuming.
* Reasoning: This generalizes a prior wildcard design's split of "exactly one" (`*`) from
  "unbounded" (`?`-as-last-token) rather than reversing it. That design's stated gap — "no way to
  express an unbounded number of args in the *middle* of a pattern" — turned out to be an
  artifact of only having one token do unbounded-anything duty, not a fundamental limit of the
  matcher: `match_from` is already a memoized DP over `(rule_index, candidate_index)`, the same
  shape of recurrence used for shell-glob/regex wildcard matching in general, and letting a token
  match "zero or more, then re-try myself" at any position is the standard third case in that
  recurrence, not new machinery. The memo table's size and the total work stay
  `O(len(rule) * len(candidate))` regardless of where `"**"` tokens sit or how many there are.

  Splitting unbounded-anywhere onto its own symbol also removes an irregularity the prior design
  left in place as an accepted narrowing: `"?"` meant "optional" everywhere except its own last
  position, where it silently meant something categorically different ("however many more args").
  A rule author scanning `["git", "status", "?"]` had to remember that `?`'s meaning depends on
  whether it happens to be the final token. With `"**"` available, `"?"` drops that position-based
  special case entirely — it always means zero-or-one — and an author who wants unbounded
  trailing args writes `["git", "status", "**"]` instead, which also reads closer to `**` in
  shell globs (`git ** status **` scans as "anything, then status, then anything," not as an
  accidental default). The one thing this still doesn't give a config author is "at most N args,
  N > 1" as a single token — `["ls", "?", "?", "?"]` for "up to three optional trailing args" is
  still spelled out explicitly, which remains the deliberate safer-failure-mode choice: nothing
  produces broader-than-intended matching by omission.
