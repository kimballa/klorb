# Default thinking to enabled at high effort

* Date: 2026-06-30 00:00
* Question: `SessionConfig` needs default values for `thinking_enabled` and
  `thinking_effort` — the two knobs that decide whether a `reasoning` request body is sent
  to a thinking-capable model, and how deep that reasoning should go. What should a fresh
  `Session` do by default, before the user has touched the command palette?
* Answer: `SessionConfig.thinking_enabled` defaults to `True`, and
  `SessionConfig.thinking_effort` defaults to `"high"`. For a model that doesn't report
  `capabilities()["thinking"] is True`, both are no-ops (`Session._reasoning_params()`
  returns `None` regardless).
* Reasoning: klorb's audience is coding/software-engineering tasks, where deeper reasoning
  measurably improves correctness on non-trivial requests, and the cost/latency tradeoff of
  the highest effort level is judged worth paying by default rather than requiring users to
  discover and opt into it per session. Defaulting off (or to a lower effort) would silently
  under-use a capability the active model already supports, on the assumption that "cheaper
  but sometimes wrong" is rarely what a user actually wants from a coding assistant. Since
  the setting is a no-op for models that don't support thinking, defaulting it on carries no
  cost for the majority of models that don't declare `thinking: True` today (e.g.
  `openai/gpt-4o-mini`, klorb's own default model).
