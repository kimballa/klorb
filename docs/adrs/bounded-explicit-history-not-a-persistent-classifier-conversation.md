# The bash risk classifier gets a bounded, explicit history block, not a persistent conversation

* Date: 2026-07-12 00:00
* Question: The bash risk classifier (`klorb.permissions.risk_classifier.classify_command_risk`)
  should take into account commands the user has already approved or denied earlier in the same
  session, so a run of very similar approvals can make it generalize a `suggested_pattern` more
  aggressively next time. Today every `classify_command_risk()` call is a single, independent,
  one-shot request — no conversation persists across calls. Should this history be delivered by
  keeping the classifier's own conversation alive across the whole session (each call appending a
  turn, pruned to the last K exchanges once it grows too long), letting the model absorb the
  history "for free" as part of its own transcript — or by keeping every call one-shot and
  explicitly passing in a bounded window of prior decisions as labeled context data?
* Answer: Keep `classify_command_risk()` fully stateless and one-shot. Add
  `klorb.permissions.risk_classifier.HistoryEntry` (a command's own text plus the user's rendered
  decision) and `record_decision_history()`, called from `klorb.tui.repl.ReplApp.
  _confirm_permission_ask` right after the user's `PermissionDecision` comes back, to append one
  entry to a plain list in `session.tool_state["BashRiskClassifierHistory"]`, trimmed to the most
  recent `tools.bash.riskClassifier.historySize` entries (default 20) on every append.
  `resolve_item_risk_assessment` reads that bounded window back out and passes it into
  `classify_command_risk(..., history=...)`, which renders it as a `<PriorDecisionsHistory>`
  element in the user message, clearly distinguished from `<CommandUnderReview>` and explicitly
  described in the system prompt as calibration context the model must never itself score.
* Reasoning: A persistent per-session classifier conversation was rejected because the thing the
  user actually wants remembered — "the user has approved a whole bunch of very similar
  commands" — is the user's own `PermissionDecision`, which today never flows into the
  classifier's own request/reply turns at all; it's recorded downstream, in `ReplApp`/`Session`,
  after the classifier has already replied. Keeping the classifier's own conversation alive would
  only let it see its *own* prior guesses played back to itself, not what the user actually
  decided — the wrong signal for the stated goal, and one that risks self-reinforcing an early
  wrong guess rather than correcting it. Getting the right signal into a persistent conversation
  would still require the exact same `record_decision_history`-shaped plumbing this ADR describes
  (something outside `classify_command_risk` recording the user's decision after the fact) — the
  persistent-conversation design doesn't actually save that work, it only changes what happens to
  the recorded data afterward.

  A persistent conversation also costs more than it buys once that plumbing exists anyway:
  `tools.bash.riskClassifier.timeout` budgets a single interactive round trip at 5 seconds because
  it blocks `PermissionAskPanel` from appearing at all, so resending a whole growing transcript on
  every ask (rather than a small labeled history block sized once, explicitly, via
  `historySize`) adds latency and cost for no accuracy benefit the explicit-history block doesn't
  already provide. It also keeps old untrusted `<CommandUnderReview>` content (and any
  prompt-injection payload it might carry — see `_SYSTEM_PROMPT`'s own untrusted-content section)
  resident in context far longer than the single request it was actually relevant to, whereas a
  `HistoryEntry` only ever carries the command text and the plain decision label, never the
  model's own prior freeform rationale, and is still wrapped in the same CDATA/never-instructions
  treatment as the item being scored.

  Finally, a stateless call keeps `classify_command_risk` easy to reason about and test — every
  existing test in `test_risk_classifier.py` drives it as a pure function of its arguments — and
  keeps `Session.tool_state`, which already owns the per-session `ItemRiskAssessment` cache this
  feature sits next to, as the one place session-scoped classifier state lives, rather than
  splitting it between that cache and a second, harder-to-inspect conversation object.
