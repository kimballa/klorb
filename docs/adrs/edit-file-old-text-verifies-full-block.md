# EditFile's old_text form verifies the entire block, not just its endpoints

* Date: 2026-07-18 16:00
* Question: `EditFileCore`'s classic `start_text`/`end_text` form anchors a replacement span on
  only its first and last line, by design — see
  [[edit-file-tolerates-bounded-line-drift-via-local-candidate-search]]'s reasoning that
  recapitulating a whole span just to anchor an edit is token-unfriendly. The new `old_text`
  form hands the harness the *entire* replacement block verbatim (the caller already pays the
  tokens to send it, since it doubles as the anchor). Should a drift-search candidate for
  `old_text` be verified against just its first/last line (mirroring the classic form), or
  against every line of the block?
* Answer: Verify the whole block. A candidate position is accepted only when every line of the
  file's span there equals the corresponding line of `old_text` (exactly, or per-line
  whitespace-tolerant on the fuzzy retry) — `EditFileCore._find_drift_candidates()`'s
  `block_lines` parameter supplants the endpoints-only `_anchor_matches()` check for an
  `old_text` call. `_resolve_line_range_edit()`'s zero-candidate error names the first interior
  line that actually differs, not just "start/end didn't match."
* Reasoning: This is strictly safer than endpoints-only — a genuine wrong-location match becomes
  less likely, never more — and it doesn't reopen the token-cost concern the drift-search ADR
  raised: that concern was about *forcing* a caller to send interior content it otherwise
  wouldn't send just to anchor an edit. Here the caller *chose* `old_text` and already sent the
  full block as the substance of the call; verifying what's already present is free safety, not
  additional burden. The classic `start_text`/`end_text` form is untouched and remains the
  token-efficient choice for a long span precisely because it doesn't require this.
