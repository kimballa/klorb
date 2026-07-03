# EditFile tolerates bounded line drift via a local candidate search, not a batch-edit API

* Date: 2026-07-02 21:10
* Question: A tool eval (`edit_file_many_ops_bottom_to_top`) showed a model applying several
  single-line `EditFile` insertions to one file top-to-bottom instead of bottom-to-top. Each
  successful insertion shifted every later line down by one, so every subsequent call's
  `start_line`/`end_line` hint (computed from the file's original state) went stale, and
  `EditFile`'s drift-verification check rejected each one with "start_text does not match."
  The model partially recovered via `ReadFile`, but at one point misread the fresh output and
  applied an edit to the wrong line, corrupting the file. Should `EditFile` gain a way to
  recover from this, and if so, how?
* Answer: Treat `start_line`/`end_line` as a location *hint* rather than a hard requirement.
  When `start_text`/`end_text` don't match exactly at the hint, search within
  `ProcessConfig.edit_file_drift_search_radius` lines of it (default 20; see
  `DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS` in `process_config.py`) for a line where they match
  together at the same relative offset — preserving the caller's requested span
  (`end_line - start_line`), so a relocation never applies to a differently-shaped interior
  than intended. Exactly one candidate → apply the edit there and report the correction back
  (`requested_start_line`/`requested_end_line` echo the input, `start_line`/`end_line` report
  where it actually landed, `line_hint_matched` says whether relocation was needed). Zero
  candidates → raise the same `ValueError` as before (still fails closed, now noting the search
  radius tried). Two or more → raise a distinctly-worded `"Ambiguous match"` `ValueError`
  listing the candidate lines, and offer new optional `context_before`/`context_after`
  parameters (a line or two of raw content immediately before/after the target, checked against
  every candidate whenever supplied) to disambiguate without needing a fresh `ReadFile`. An
  out-of-bounds hint (`end_line > total_lines`, etc.) still raises immediately, with no search
  attempted at all — this only recovers content drift once the hint is nominally in range.

  A multi-edit batch API (accepting a list of edits in one call, applied bottom-to-top
  internally) was considered and rejected: it only protects edits packaged into the same call,
  and an agent making several separate `EditFile` calls across a turn — the actual shape of the
  eval failure — would still hit the same staleness. It also would have added meaningful schema
  surface (list-of-edits, overlap detection between entries) for a narrower fix. Making a single
  edit call itself drift-tolerant fixes both cases.
* Reasoning: Bounded-radius search was chosen over an unbounded whole-file search because real
  drift from edits earlier in the same turn is inherently local — an unbounded search would
  raise the odds of coincidentally matching an unrelated, identical line far away (a bare `}`,
  a blank line) and silently editing the wrong place. Failing closed (raising `ValueError`) on
  zero or multiple candidates, rather than guessing by proximity, keeps that same
  never-silently-wrong-file property `EditFile`'s original drift check already had. Proximity is
  used only to bound the search, never to break a tie. `context_before`/`context_after` are
  optional and checked only for the lines immediately around the target — never the whole
  deleted range — since the entire point of the eval that motivated this was to avoid a
  "token-unfriendly" design that recapitulates large spans of file content just to anchor an
  edit; `EditFile` already only anchored on the single line at `start_line` and the single line
  at `end_line` before this change, and that stays true here.

  This is scoped to the drift direction actually observed (insertions, which push later line
  numbers up): a file that's *shrunk* enough to put the hint's `start_line`/`end_line` outside
  `[1, total_lines]` still hits the unchanged hard "out of range" error with no search fallback,
  a deliberate, narrower scope than "recover from any drift in any direction."

  A follow-up round of evals covering repeated-line files (`edit_file_ambiguous_match_*` in
  `klorb/evals/cases.py`) surfaced a second-order version of the same underlying problem: a
  model that correctly noticed an "Ambiguous match" error and correctly decided to retry with
  `context_before`/`context_after` still sometimes supplied text that wasn't actually adjacent
  to its intended line — reconstructing "what comes right before/after" from memory of an
  earlier `ReadFile` call and miscounting by one line, the same class of mistake the drift
  search itself exists to route around, just relocated into the context parameters instead of
  `start_line`/`end_line`. The fix: the "Ambiguous match" error now names each candidate's
  actual nearby content directly, as the literal `context_before=...`/`context_after=...` value
  that singles it out (`_describe_candidate_neighbors`), so a model can copy it verbatim for
  whichever location it means rather than reconstruct that text itself.

  How much context to show per candidate is computed adaptively
  (`_minimal_disambiguating_window`), not a fixed length: starting from 1 line on each side, it
  expands only as far as needed until every candidate is uniquely singled out by its own
  natural surrounding content (checked via the real `_context_matches_candidate` machinery, not
  a naive string comparison — an early naive version that just compared each candidate's
  `(before, after)` window pair as a tuple over-credited an *empty* window as if it were a
  usable, distinguishing value, when an empty `context_before`/`context_after` is actually
  unusable as a positive constraint; see the next paragraph for the fix that made this
  distinction expressible at all). This expansion is provably guaranteed to fully resolve every
  candidate, with no permanent-tie case at all: once a candidate's window has grown to its own
  true maximum on both sides (every line actually available before it, every line actually
  available after it), no other candidate can share that same `(before, after)` line-count pair
  — a different line can't be the same distance from both the file's start and its end — so
  `_context_matches_candidate`'s bounds checks alone rule out every other candidate at that
  point, independent of content. A single fixed preview length (2, chosen originally) handled
  the common cases this eval round started from, but provably could never resolve a "true
  middle" position in a run of 5+ identical lines bounded by anchors on both sides — the
  adaptive version has no such ceiling and always terminates with a unique resolution, bounded
  only by the file's own length.

  A related gap surfaced alongside this: an omitted `context_before`/`context_after` argument
  and an explicitly-supplied empty string `""` were indistinguishable (both defaulted to `""`
  in `apply()`), yet they mean opposite things — "don't check this side" vs. "assert this side
  is genuinely empty" (the candidate is the file's actual first/last line). Without that
  second meaning, a candidate truly at a file's start/end could only ever be pinned down via
  the *other* side's context, even though "it's the first/last line" is itself often the single
  strongest, cheapest fact available. The fix: `context_before`/`context_after` are now `None`
  when the argument is omitted (skip that side's check, as before) and a real, checked
  assertion when explicitly `""` (`candidate_start`/`candidate_end` must equal the file's
  actual first/last line) — see `_context_matches_candidate`. This also made the adaptive
  window's own boundary-candidate math simpler and cheaper: `_candidate_resolved_at`'s
  natural-window simulation for a candidate at a file's edge now produces this same meaningful
  `""` assertion rather than a value that silently skipped the check, so boundary candidates
  routinely resolve in fewer lines of context than an interior candidate needs.
