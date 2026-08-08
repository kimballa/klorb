# Count every message's tokens client-side with tiktoken, not from provider usage stats

* Date: 2026-07-14 00:00
* Question: [[null-estimated-tokens-when-a-real-count-supersedes-them]] and
  [[derive-user-turn-token-counts-from-a-prompt-token-delta]] built the context-usage footer
  around a provider's own aggregate, per-request `prompt_tokens`/`completion_tokens` usage figures:
  a running scalar (`Session._last_known_real_tokens`) refreshed from the latest round's real
  totals, a per-message `estimated_tokens` field bridging the gap live during streaming, and a
  delta (`prompt_tokens - tokens_before`) attributed to each user turn after the fact, since no
  provider hands back a genuine per-message breakdown. In practice, this "chunkiness" makes the
  footer hard to trust: the reconciled number depends on exactly when a provider's `usage` object
  arrives and what it bundles (whether reasoning tokens are folded into `completion_tokens` or
  reported separately varies by provider), and it can never be verified against any single message.
  Should klorb keep reconciling against provider-reported usage at all?
* Answer: No. Every `Message.num_tokens` (`klorb/src/klorb/message.py`) is now always a client-side
  `tiktoken` count of that message's own content (`klorb.token_estimate.estimate_tokens`), set once
  at construction (or refreshed on every streamed chunk for an in-progress placeholder) and treated
  as that message's definitive, permanent cost — never superseded by a server-reported figure. The
  `estimated_tokens` field, `Session._last_known_real_tokens`, `Session._settle_estimated_tokens()`,
  and `Session._tokens_recorded_so_far()`/`_dispatch_turn(tokens_before=...)`'s prompt-token-delta
  derivation are all removed; a user message's `num_tokens` is simply `estimate_tokens(prompt)` at
  construction, same as every other message role.

  `Session.total_tokens_used()` sums every message's `num_tokens`, except a `role="thinking"`
  message when the active model's `Model.drop_reasoning()` is `True` — the same predicate
  `OpenRouterApiProvider._build_api_messages()` applies when building the next request, so the
  footer always reports exactly what would be uploaded at the start of the next turn (see
  [[preserve-reasoning-across-turns-by-default]]). `Session.total_output_tokens_used()` sums
  `num_tokens` over `"assistant"`/`"tool_use"`/`"thinking"` messages unconditionally, since it
  reports everything the model has generated rather than what would be resent.

  `OpenRouterApiProvider.send_prompt()` still reads `chunk.usage.completion_tokens`/`prompt_tokens`
  from the provider and logs them, and `ProviderResponse.prompt_tokens` still carries the real
  request-level total through — but purely for diagnostics; nothing in `Session` reads either
  value anymore.
* Reasoning: The two superseded ADRs' whole rationale — a full local re-tokenization "not worth it
  for a number that's discarded within one round trip" — assumed provider usage figures were the
  source of truth worth reconciling against, and a client-side estimate was only ever a stand-in
  until the real number arrived. That assumption is what's being reversed here: a provider's
  aggregate usage is reported per-request, not per-message, bundles reasoning/tool-call-argument
  tokens inconsistently across providers, and arrives on its own schedule relative to the stream —
  none of which klorb can rely on to attribute an exact, stable count to any one message. A
  `tiktoken`-based estimate, by contrast, is fully under klorb's control: same encoding, same
  content, same answer every time, for every message, both live while streaming and forever after.
  It won't match any given provider's own tokenizer exactly, but it was already klorb's own
  yardstick for the live-during-streaming case (`klorb.token_estimate`, already a project
  dependency) — this ADR just stops treating it as inferior to a number that turns out not to be
  trustworthy either.

  This is a deliberate trade: per-message attribution is no longer *exact* against what a provider
  actually billed, in exchange for being self-consistent, always available, and never dependent on
  reconciling against a delayed or ambiguous server figure. Given that the whole point of the
  footer is to give the user a stable, comparable sense of context usage turn over turn — not to
  reproduce an invoice — consistency was judged more valuable than provider-exactness. This also
  simplifies `Session` meaningfully: no more real/estimate split to reason about per message, no
  settle-on-success sweep, no delta arithmetic for the user turn — every message just carries its
  own number from the moment it exists.
