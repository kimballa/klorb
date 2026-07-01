# Derive user-turn token counts from a prompt-token delta

* Date: 2026-06-30 00:00
* Question: There is no per-message tokenizer available to klorb, so how should `Session`
  fill in a user `Message`'s `num_tokens`, given that the provider only reports aggregate
  `prompt_tokens`/`completion_tokens` for the whole request?
* Answer: `ApiProvider.send_prompt()` takes the full `list[Message]` history (plus a
  separate `system_prompt` string, not itself stored as a `Message`) and returns a
  `ProviderResponse` (`message: Message`, `prompt_tokens: int`). `Session` sets the
  assistant reply's `num_tokens` directly from `completion_tokens` (already on the returned
  `Message`). For the user's own turn, once the response arrives it computes
  `prompt_tokens - sum(num_tokens for message already recorded before this turn)` and
  assigns that delta as the user `Message`'s `num_tokens`.
* Reasoning: The provider only bills for the whole request, not per message, so exact
  per-message counts aren't obtainable without also running a local tokenizer — an
  additional dependency and a source of drift from the provider's actual count. A delta
  against the running total is exact by construction (it always reconciles to
  `prompt_tokens` for that request), at the cost of bundling the untracked system prompt's
  real token cost into the very first user turn's delta, since the system prompt is
  re-derived fresh from `Model.system_prompt()` on every call rather than stored as its own
  `Message`. This is an accepted approximation for v1.
