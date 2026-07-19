# EditFile reinterprets a multi-line start_text as old_text only when end_text is absent

* Date: 2026-07-18 16:00
* Question: Before the `old_text` form existed, `EditFileCore` already tolerated a model pasting
  a multi-line block into `start_text` by truncating it to its first line (with an advisory
  `feedback` note) and anchoring on that alone. Now that a multi-line block has a first-class
  home (`old_text`), should a multi-line `start_text` always be reinterpreted as `old_text`
  (superseding the truncation behavior entirely), or only in some narrower case?
* Answer: Reinterpret a multi-line `start_text` as `old_text` only when `end_text` is empty or
  missing. When `end_text` is also present (and non-empty), the pre-existing truncate-to-
  first-line behavior applies unchanged, `end_text` still anchors the end, and the same advisory
  `feedback` is emitted.
* Reasoning: Always converting would reopen a semantics question that isn't settled: if a caller
  supplies both a multi-line `start_text` *and* a non-empty `end_text`, should `end_text` be
  read as the last line of the intended `old_text` block, or as the line *after* it? Both are
  plausible reconstructions of caller intent and nothing in the existing call shape disambiguates
  between them. Restricting the conversion to the `end_text`-absent case sidesteps that ambiguity
  entirely — there's only one field carrying content, so there's only one reasonable
  interpretation of it — while still turning the common "pasted the whole block into
  `start_text`" mistake into a useful edit instead of a lossy truncation. The narrower case
  covers what's actually been observed; if agents are later seen reaching for the combined
  multi-line-`start_text`-plus-`end_text` shape in practice, that's new evidence to revisit this
  decision with, rather than a semantics choice made speculatively now.
