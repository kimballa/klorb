# Null estimated_tokens when a real count supersedes them

* Date: 2026-07-10 00:00
* Question: `Message.num_tokens` is only settled with a real, server-reported count once a
  round completes (see [[derive-user-turn-token-counts-from-a-prompt-token-delta]]), so the
  context-usage footer has nothing to show for content already typed or streamed but not yet
  round-tripped. How should klorb give the footer a live, updating number without a local
  tokenizer, while reconciling exactly with the provider's real counts once they arrive — and
  without double-counting a round's own reply once it's resent as history to a later round?
* Answer: Every `Message` gets a second field, `estimated_tokens: int | None`
  (`klorb/src/klorb/message.py`), filled in immediately from `klorb.token_estimate.
  estimate_tokens` (a `tiktoken` `o200k_base`-encoding count — see that ADR's rejected
  alternative below) the moment content exists for it: at construction for a bookkeeping
  (`system`/`tool_defs`), user, or `tool_response` message, and on every streamed chunk for an
  in-progress assistant/thinking placeholder.

  The footer-facing total is *not* a sum of every message's own `num_tokens` — see
  "Reasoning" for why that double-counts. Instead, `Session` tracks one running scalar,
  `_last_known_real_tokens`, updated after every completed round (not just a turn's last one)
  in `_send_and_receive`'s success path: `result.prompt_tokens + result.message.num_tokens`.
  That sum is, by construction, the exact current size of the whole conversation as of that
  round, since `prompt_tokens` already reflects every message just sent as input to it,
  however many earlier rounds contributed to that history. The same success path also calls
  `Session._settle_estimated_tokens()`, which clears `estimated_tokens` on *every* message
  currently in the buffer — nothing needs its own estimate anymore once `prompt_tokens` has
  accounted for all of it. `Session.total_tokens_used()` reports
  `_last_known_real_tokens + sum(estimated_tokens or 0 across all messages)`, so the footer
  always has a number, live during streaming and exact the instant each round settles.

  The one case left with a non-`None` estimate after a round completes is a reply/thinking
  placeholder still marked `processing_state="aborted"`: it was interrupted mid-stream, so it
  was never part of any completed round's input or output, and no real count is ever coming
  for it — `_settle_estimated_tokens()` only ever runs from the success path, so an aborted
  placeholder is simply never touched, and its estimate is the only number it will ever have.
* Reasoning: An earlier design summed every message's own `num_tokens` for the total (as
  `_tokens_recorded_so_far()` already did for the unrelated per-message delta baseline — see
  [[derive-user-turn-token-counts-from-a-prompt-token-delta]]) and cleared `estimated_tokens`
  per message as each one settled. That double-counts a multi-round tool-calling turn: round
  1's reply gets its own real `num_tokens` (its `completion_tokens`) the moment round 1
  finishes; that same reply then gets resent as history and is included again in round 2's
  `prompt_tokens`, which is what round 2's *own* delta-settled number is derived from. Summing
  both — the reply's standalone count *and* its share of the next round's delta — bills it
  twice. Concretely, for a two-round turn (round 1: `prompt_tokens=10`, `completion_tokens=3`;
  round 2: `prompt_tokens=20`, `completion_tokens=4`), summing every message's `num_tokens`
  gives `0 (system) + 0 (tool_defs) + 20 (user, delta) + 3 (round‑1 reply) + 0 (tool_response) +
  4 (round‑2 reply) = 27`, but the conversation's real current size — what actually matters
  against `max_context_window`, since that's a per-request cap, not a cumulative spend counter —
  is just round 2's own `prompt_tokens + completion_tokens = 24`. Tracking
  `_last_known_real_tokens` as a single scalar, refreshed from the latest round's own
  `prompt_tokens + completion_tokens`, sidesteps the double-count entirely instead of trying
  to patch it message-by-message; the existing per-message `num_tokens` delta convention
  (`_tokens_recorded_so_far()`, `user_message.num_tokens`) is untouched, since it exists for a
  different purpose (attributing *some* number to each message) and other consumers may still
  read it.

  A real per-message tokenizer was rejected for exactness of *individual message* attribution
  the same way [[derive-user-turn-token-counts-from-a-prompt-token-delta]] rejected one: no
  provider hands back a per-message breakdown, only aggregate `prompt_tokens`/
  `completion_tokens` per request, so nothing short of a full local re-tokenization of the
  provider's own wire format would attribute exactly — not worth it for a number that's
  discarded within one round trip. `klorb.token_estimate` does use a real tokenizer
  (`tiktoken`, already a project dependency) rather than a character-count heuristic, though:
  unlike per-message attribution, a *single-message* estimate's whole job is to closely match
  what the provider will eventually report for that content, and `tiktoken`'s `o200k_base`
  encoding gets close for any provider's tokenizer at negligible cost, with no reason to settle
  for a cruder guess.
