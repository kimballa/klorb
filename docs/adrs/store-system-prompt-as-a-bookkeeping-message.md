# Store the system prompt as a bookkeeping `system` Message, mirroring `tool_defs`

* Date: 2026-07-01 22:55
* Question: `Session._ensure_tool_defs_message()` inserted its `tool_defs` bookkeeping
  message "conceptually right after the system prompt," but the system prompt itself was
  never inserted into `self._messages` — it only ever existed as a value threaded through
  `_dispatch_turn`/`_send_and_receive` into `ApiProvider.send_prompt(system_prompt=...)`.
  Should the system prompt actually be recorded in session history, the way tool
  definitions already are (see
  [[wire-tool-calling-into-the-session-turn-loop]])?
* Answer: Yes. `Session._ensure_system_message()` inserts a `role="system"` `Message` at the
  very front of `self._messages`, the first time a turn is dispatched, holding whatever
  `Model.system_prompt()` returned for the active model at that point. Like
  `_ensure_tool_defs_message`, this is a one-time, no-op-if-already-present insert — it
  doesn't track later changes to `config.model`. `_ensure_tool_defs_message` now inserts
  after this message (index `1`) instead of always at index `0`, so history reads
  `system, tool_defs, user, ...`. The stored message is bookkeeping only:
  `OpenRouterApiProvider._build_api_messages` drops `"system"`-role messages from history
  (alongside `"thinking"` and `"tool_defs"`), and the live system message sent to the model
  continues to be built from the `system_prompt` argument, which `_dispatch_turn` still
  re-derives fresh from the *current* active model on every round trip.
* Reasoning: Recording the system prompt in `self._messages` gives klorb's own history/TUI a
  place to show what was actually sent, the same motivation `_ensure_tool_defs_message` had
  for tool definitions — without it, nothing in history explains the model's behavior in
  terms of its instructions. Keeping the live `system_prompt` argument (not the stored
  message) as the actual driver of the request preserves the existing "recomputed fresh
  every turn" behavior: if a session's `config.model` changes mid-session (e.g. via the
  command palette), the next turn still sends that model's real system prompt, even though
  the bookkeeping message from the first turn is now stale — exactly as `tool_defs` already
  tolerates going stale if the tool registry changes after insertion. The system prompt's
  token cost continues to be folded into the first user turn's `num_tokens` delta rather
  than given its own count (see
  [[derive-user-turn-token-counts-from-a-prompt-token-delta]]); this is unaffected by where
  the prompt text lives.
