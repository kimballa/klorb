# Map thinking effort levels to fixed token budgets for token-budget-style models

* Date: 2026-06-30 00:00
* Question: Some thinking-capable models (`Model.capabilities()["thinking_budget_style"]
  == "tokens"`, e.g. Anthropic-style extended thinking) want a numeric reasoning token
  budget rather than an `"effort"` keyword. `SessionConfig` only exposes one user-facing
  knob, `thinking_effort` (`"low" | "medium" | "high"`), for both kinds of model. How should
  `Session` translate that knob into a token budget for a `"tokens"`-style model?
* Answer: `klorb.session.THINKING_EFFORT_TOKEN_BUDGETS` is a fixed
  `dict[ThinkingEffort, int]` — `{"low": 4_096, "medium": 16_384, "high": 32_768}` — keyed
  by the same three levels offered for `"effort"`-style models.
  `Session._reasoning_params()` looks up `THINKING_EFFORT_TOKEN_BUDGETS[config.
  thinking_effort]` and sends `{"max_tokens": <budget>}` instead of `{"effort": <level>}`
  when the active model's `thinking_budget_style` is `"tokens"`.
* Reasoning: Exposing a second, model-specific numeric-budget setting in `SessionConfig`
  (and a second set of command-palette commands) would double the user-facing surface area
  for a single underlying concept — "how hard should the model think" — and would require
  the user to know which style their currently-selected model wants. A fixed mapping keeps
  one knob, `thinking_effort`, working uniformly across both model families; switching
  models never requires touching thinking config again. The specific token counts are a
  reasonable v1 default (roughly doubling per step, topping out well under most models'
  reasoning budgets) rather than a value tuned per model; revisiting them (or letting a
  `Model` override its own per-level budgets) is left for when a real token-budget-style
  model is wired in and its actual behavior can inform the numbers.
