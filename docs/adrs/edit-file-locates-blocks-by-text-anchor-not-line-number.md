# EditFile locates the block to replace by text anchor alone, never a line number

* Date: 2026-08-06 00:00
* Superseded: [[edit-file-tolerates-bounded-line-drift-via-local-candidate-search]],
  [[edit-file-exact-hint-match-skips-ambiguity-scan]],
  [[edit-file-hint-aliases-as-bare-schema-properties]],
  [[edit-file-form6-only-converts-without-end-text]],
  [[edit-file-old-text-verifies-full-block]],
  [[edit-file-auto-creates-via-empty-subject-insert-shape]],
  [[edit-file-covers-insert-and-delete-via-replace-range]] (the mechanism each describes; see
  each file's own "Superseded by" note for what specifically changed).
* Question: `EditFileCore`'s interface had accreted into a wide accepted-argument matrix: a
  classic `start_line`/`end_line`/`start_text`/`end_text` form, a single-line shortcut, an
  `old_text` form with its own optional hint and inferred `end_line`, four line-hint alias
  spellings, an implicit `start_text`→`old_text` conversion, a bounded-radius drift search when
  the hint didn't match exactly, an "exact hint skips the scan" fast path, and a separate
  `context_before`/`context_after`/`context_before_start`/`context_after_end` disambiguation
  mechanism for ambiguous matches. Each addition fixed a real problem in isolation, but the
  result asked a model to track many interacting rules, and every line-number argument required
  the model to compute or re-derive an offset it usually had no reliable way to get right after
  even one prior edit in the same turn. Could this be simplified without losing the
  drift-tolerance and disambiguation guarantees the old design existed to provide?
* Answer: Drop every line-number argument (`start_line`, `end_line`, and all four hint
  aliases) and the separate `context_before`/`context_after` mechanism entirely. Two mutually
  exclusive forms locate the block to replace, both purely text-based:
  * `old_text` alone — the entire replacement block, verbatim. Must match exactly one location.
  * `old_text_start`/`old_text_end` together — each must itself match exactly one location;
    everything from `old_text_start`'s match through `old_text_end`'s match, inclusive, is
    replaced.

  Both require whole file lines, never a sub-line fragment — enforced implicitly, since matching
  is always a contiguous-line-window comparison. Matching is exact-first, falling back to a
  whitespace/punctuation-tolerant comparison (leading/trailing whitespace stripped; em/en dash
  and minus sign folded to a plain hyphen; curly double/single quotes folded to straight ones)
  only when the exact search finds nothing, and only honored if that fallback resolves to
  exactly one location.

  An ambiguous match (more than one location) raises `ValueError` listing ready-to-use candidate
  JSON fragments — one per matching location — each extending the anchor(s) outward with more
  surrounding context until every candidate is uniquely distinguishable, with that same extra
  context recapitulated, unchanged, in the candidate's own `new_text`. This replaces
  `context_before`/`context_after` as the disambiguation mechanism: instead of a *second* pair of
  arguments the model has to learn and get right (including the `context_before_start`/
  `context_after_end` boolean sentinels that pair needed to express "nothing on this side"), the
  growing context is folded directly into the same `old_text`/`old_text_start`/`old_text_end`/
  `new_text` fields the model already sends — the error's candidates are copy-paste-ready calls,
  not a new argument shape to construct.

  The old empty-subject insert sentinel (`start_line=1, end_line=0, start_text="", end_text=""`)
  becomes `old_text=""`, valid only when the subject is missing or empty — still the one shape
  that both creates a nonexistent file/memory and inserts into a genuinely empty one.
* Reasoning: Matching by content instead of position makes the entire drift-search apparatus —
  the bounded search radius, the "exact hint skips ambiguity" fast path, the
  `line_hint_matched`/`requested_start_line`/`requested_end_line` result fields, the alias
  spellings for the hint — categorically unnecessary rather than merely simplified: there is no
  hint to drift from, so there is nothing to tolerate drift *in*. A model that inserts a line and
  then edits further down the same file needs no special ordering discipline (bottom-to-top, or
  a drift-tolerant re-read) at all, since every subsequent match is still found by its own
  content regardless of what line it's now on.

  Folding disambiguation context into `old_text`/`new_text` themselves (rather than a parallel
  `context_before`/`context_after` pair) removes an entire argument pair, its two boolean
  sentinel escape hatches, and the asymmetry between "how you name the block" and "how you prove
  which occurrence you mean" — after this change there is only ever one way to name a location:
  more precise text.

  The dash/quote-folding fuzzy fallback is new, not merely carried over: model-generated text
  reliably reproduces a source file's em dashes, en dashes, and curly quotes with whichever
  variant its own tokenizer favors, which is a common, harmless source of an otherwise-exact
  match failing. Treating those substitutions the same way the pre-existing whitespace-tolerant
  fallback treats incidental indentation differences — attempted only after an exact search
  finds nothing, and only trusted when it resolves to exactly one location — extends an
  already-accepted tradeoff (a small, well-scoped robustness net that never weakens the
  uniqueness guarantee) rather than introducing a new one.
