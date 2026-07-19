# EditFile accepts an exact start_line/anchor match immediately, without an ambiguity scan

* Date: 2026-07-18 20:17
* Question: [the drift-tolerance ADR](edit-file-tolerates-bounded-line-drift-via-local-candidate-search.md)
  established that `EditFile` never trusts an anchor match at the hint in isolation — even when
  `start_text`/`end_text` (or `old_text`) match exactly at `start_line`, the tool still scans
  `edit_file_drift_search_radius` lines around it for other matches, and raises `"Ambiguous
  match"` if more than one exists, so a file with repeated/templated lines never gets silently
  edited at the wrong occurrence. In practice this means a model that reads a file, correctly
  computes the exact line it wants, and calls `EditFile` with a `start_line`/`start_text` (or
  `old_text`) that matches perfectly still eats an `"Ambiguous match"` error whenever the same
  content happens to repeat elsewhere in the file — even though it named an exact, correct
  location and there was nothing to disambiguate from its own point of view. Should an exact
  hint match short-circuit the scan?
* Answer: Yes. When a caller supplies a real line hint (`start_line`, or one of its aliases —
  i.e. not `old_text`'s "no hint at all" `unbounded` search mode) and the anchor already matches
  exactly there, `EditFileCore._resolve_line_range_edit()` applies the edit at that hint
  immediately and returns, without calling `_find_drift_candidates()` at all. The full scan
  (and its `"Ambiguous match"` / drift-relocation machinery) still runs in exactly the two cases
  where it's the only way to find the right location:
  * **Drift correction** — the anchor does *not* match at the hint, so the hint alone can't be
    trusted and a nearby search is the only way to recover.
  * **Search mode** — `old_text`'s `unbounded` form, which has no hint to match against in the
    first place.

  It also still runs whenever the caller explicitly supplies `context_before`/`context_after`
  (or `context_before_start`/`context_after_end`), even alongside a hint that matches exactly —
  an explicit context argument is read as "check this against other candidates too," so that
  case keeps the full disambiguation behavior unchanged. This is checked in
  `_resolve_line_range_edit()` right after the out-of-range check and before the candidate scan.
* Reasoning: This is a deliberate narrowing of the "never silently editing the wrong location"
  guarantee the drift-tolerance ADR established, traded for removing a real, recurring friction:
  a model that already did the work of computing the exact right line eats an error (and a
  `context_before`/`context_after` round-trip) purely because *other, unrelated* lines elsewhere
  in the file happen to share the same text — information the model's own exact hint already
  made irrelevant to its intent. Requiring proof of uniqueness for a call that already names an
  exact, correct location optimizes for a rarer failure mode (a *wrong* but coincidentally
  anchor-matching hint) at the cost of a common one (needless friction on a *correct* hint).

  The narrowing is real, not cosmetic: a model whose line-counting is off by one, landing on a
  *different* occurrence of the same repeated content, now gets a silent edit at the wrong
  occurrence instead of an `"Ambiguous match"` catching the mistake — exactly the failure mode
  the original ADR was designed to prevent. This tradeoff is accepted deliberately, on the
  premise that a model which already read the file and computed a specific line number is
  usually right, and that `context_before`/`context_after` (now easier to reach for via
  `context_before_start`/`context_after_end`, see
  [the boolean-sentinel note in the tool-framework spec](../specs/tool-framework.md)) remain
  available as an explicit, opt-in safety net whenever a model wants the stronger guarantee back
  — supplying either one still routes through the full scan even when the hint matches exactly.

  The four `edit_file_ambiguous_match_*` eval cases in `klorb/evals/cases.py` (added per the
  drift-tolerance ADR to prove a model reaches for `context_before`/`context_after` when facing
  repeated content) no longer require that shape: since an exactly-computed `start_line` now
  succeeds on its own, their `check()` functions were simplified to grade file state only,
  dropping the tool-call-shape assertion that would otherwise wrongly fail a model taking the
  new, more direct path.
