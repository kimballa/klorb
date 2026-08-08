# Preserve reasoning across turns by default, via a per-model drop_reasoning flag

* Date: 2026-07-14 00:00
* Question: [[exclude-thinking-messages-from-outgoing-api-requests]] decided that
  `role="thinking"` `Message`s are never replayed as conversation history — `OpenRouterApiProvider.
  _build_api_messages()` drops them unconditionally on every outgoing request. OpenRouter's own
  guidance for reasoning-capable models is the opposite: "when continuing a conversation, preserve
  the complete `reasoning_details` when passing messages back to the model so it can continue
  reasoning from where it left off." Klorb also never captured `reasoning_details` — OpenRouter's
  structured reasoning payload (`reasoning.text`/`reasoning.summary`/`reasoning.encrypted`-typed
  entries, the lossless form some providers need to verify or continue an encrypted/redacted
  reasoning trace) — at all, only the flattened plain-text `reasoning` delta. Should klorb keep
  discarding reasoning on every turn, and should it capture `reasoning_details` in the first place?
* Answer: No, and yes. `Message` gains a `reasoning_details: list[dict[str, Any]] | None` field
  (`klorb/src/klorb/message.py`), populated on a `role="thinking"` message alongside its existing
  plain-text `content`. `OpenRouterApiProvider.send_prompt()` accumulates `choices[0].delta.
  reasoning_details` fragments by `index` (mirroring the existing `delta.tool_calls` reassembly
  pattern — string-bearing fields in `_REASONING_DETAIL_INCREMENTAL_TEXT_FIELDS` concatenated across
  fragments, every other field overwritten) and reports the running merge through a new
  `on_reasoning_details` callback, threaded through `ApiProvider.send_prompt()` and
  `Session.TurnEventHandlers` the same way `on_thinking_chunk` already is.

  `Model` gains a `drop_reasoning() -> bool` method (default `False`), and `_ConfiguredModelData`/
  `ConfiguredModel` gain a matching `drop_reasoning: bool = False` JSON field, so every existing
  packaged model keeps replaying reasoning without any JSON file needing an update. `Session.
  _drop_reasoning()` reads the active model's `drop_reasoning()` and passes it to `ApiProvider.
  send_prompt(drop_reasoning=...)` on every round.

  `OpenRouterApiProvider._build_api_messages()` no longer drops `"thinking"`-role messages
  unconditionally. Since the OpenAI-compatible chat completions API has no `"thinking"` role, a
  `"thinking"` message is still never sent as its own chat entry — instead, when `drop_reasoning`
  is `False` (the default), its `content`/`reasoning_details` are folded onto the very next
  `"assistant"`/`"tool_use"` reply's own request dict as `reasoning`/`reasoning_details` fields,
  the shape OpenRouter expects for replayed reasoning. This relies on `Session._send_and_receive()`
  always appending a `"thinking"` placeholder immediately ahead of the reply it belongs to (an
  existing invariant, unchanged by this ADR) — pairing by adjacency is exact, not a heuristic. When
  `drop_reasoning` is `True`, a `"thinking"` message's content is discarded entirely, exactly as
  every model behaved before this change.

  The TUI renders a compact `<Reasoning>` indicator (e.g. `"[3 reasoning blocks preserved, 1
  encrypted]"`, via `klorb.tui.repl._summarize_reasoning_details()`) below the existing
  `<Thinking>` block, but only when `reasoning_details` carries an entry with no `text`/`summary`
  string field of its own (an opaque/encrypted entry, or a future type klorb doesn't recognize).
  When every entry has its own readable text, the indicator is suppressed: OpenRouter's flattened
  `reasoning` delta is typically composed from those very entries, so the plain `<Thinking>` block
  already shows the same content, and a second rendering would just be noise.
* Reasoning: Reasoning content in `Session._messages` was always meant for display/debugging (the
  original ADR says as much), but "not meant to be replayed" turned out to be too strong a rule —
  it directly contradicts what reasoning-capable providers document as best practice, and for some
  providers (encrypted/redacted reasoning items) omitting `reasoning_details` doesn't just lose
  context, it can break the reasoning trace's continuity or signature verification on the model's
  side entirely. A blanket exclusion also meant every reasoning-capable model paid the cost of
  restarting its reasoning cold on every follow-up turn, tool-call round included, regardless of
  whether the specific provider behind that model wanted or needed the replay.

  A model-level opt-out (rather than a session- or request-level one) was chosen because whether
  reasoning should be replayed is a property of the model/provider combination, not of the
  conversation: a provider that doesn't support resending reasoning (or actively rejects it) needs
  this off for every session using that model, and `Model`/`ConfiguredModel` is already the layer
  that carries per-model capability flags (`thinking`, `thinking_budget_style`, `klorb_capabilities`)
  read by `Session` — adding one more here needed no new plumbing. Defaulting every model to `False`
  (preserve) rather than `True` (drop, matching the old universal behavior) was deliberate: OpenRouter's
  documented recommendation is the norm across providers, and a model that genuinely can't handle
  replayed reasoning is the exception that should declare so explicitly, not the default assumption.

  Folding reasoning onto the *next* reply's request dict (rather than, say, giving `"thinking"` its
  own accepted role) was chosen because that's the actual wire shape OpenRouter/OpenAI-compatible
  APIs use: reasoning is a property of one assistant turn, not a freestanding message of its own.
  Pairing by adjacency avoids needing any explicit "this thinking belongs to that reply" linkage in
  `Message` itself.

  The TUI's suppress-when-redundant rule for `reasoning_details` was chosen over always rendering it
  (simpler, more consistent, but visually noisy — most models would show the same text twice) and
  over a full raw-JSON dump (maximally faithful, but would routinely surface opaque base64 blobs and
  provider bookkeeping fields no user needs to see). A compact indicator that only appears when it
  adds information keeps the transcript readable while still confirming, for the models where it
  matters, that reasoning was captured and will be resent.
