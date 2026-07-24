# Plan 016, increment 012: Queued mid-turn messages and interrupt polish

> **Draft.** Part of [Plan 016](plan-016-acp-client-server.md). Do not implement from the
> `drafting/` folder.

## Goal

Close the last functional gaps against the TUI: submitting a message while a turn is in
flight queues it into the *running* turn (klorb's `user_interjections` capability — see
docs/specs/session-and-turns.md's `user_interjections` bullet) instead of being rejected,
with the TUI's queued-then-delivered visual transition; and the abort/interrupt/error
surfaces get their final polish pass. Both sides in one increment — the feature is a
single conversation loop.

## Server deliverables (python)

* `_klorb/enqueueMessage` ext request (client → agent): params
  `{sessionId, text}`, result `{queued: true}`. Valid only while a prompt is in flight
  (JSON-RPC error otherwise — the client should have sent a normal `session/prompt`);
  calls `Session.enqueue_queued_message(...)`.
* `TurnBridge` registers `on_enqueue_message` and `on_send_queued_message`, emitting
  `_klorb/messageQueued {sessionId, text}` and `_klorb/queuedMessageSent {sessionId,
  text}` notifications on the ordered queue — the client's cue to render the italic
  queued entry and later flip it to delivered styling (the `QueuedMessage.history_data`
  opaque slot stays unused server-side; correlation is by order+text, matching the
  single-queue reality). Advertise `agentCapabilities._meta.klorb.enqueueMessage = true`.
* Turn-end behavior: klorb's existing `Session` machinery already redelivers a message
  still queued at turn end as the next turn; verify how that surfaces server-side and
  make the bridge run the follow-on turn inside the same `session/prompt` scope if
  that's what `Session` does, or (if it requires a new `send_turn`) drive it from
  `prompt()` before resolving — matching TUI behavior exactly; record the observed
  contract in the spec.

## Client deliverables (typescript)

* Prompt input stays *enabled* during a turn (revising 002's disable): submitting
  mid-turn posts `enqueueMessage` via the ext method (capability-gated; without it,
  fall back to 002's disabled-input behavior). New host messages `messageQueued` /
  `queuedMessageSent` → history entries in italic "Queued message" styling that flip to
  regular prompt styling on delivery (TUI parity).
* Interrupt polish:
  * Stop button + Escape cancel path (002) now also renders the TUI's
    "(interrupted)" marker on whichever streaming entry was live, or a standalone
    interrupted notice when none was (port the `_handle_aborted_response` decision
    table into a pure model function).
  * Turn errors render as `.error`-styled entries with the message text; a lost server
    process (child exit mid-turn) surfaces a distinct entry plus a "Restart server"
    action button wired to the existing command.
* Sweep pass, explicitly in scope here: input re-enable/focus discipline after every
  panel/turn path, history autoscroll behavior (stick to bottom unless the user has
  scrolled up), and `sessionReset` clearing all model slots (tasks, status, pending
  interactions).

## Tests

* Python (`test_acp_server_queued_messages.py`, harness): enqueue during a scripted
  multi-round tool turn → interjection reaches the tool-response envelope (assert via
  the provider-visible request payload, the same style the session suite uses),
  `messageQueued`/`queuedMessageSent` notifications bracket it in order; enqueue with
  no turn in flight → error; message still queued at turn end becomes the next turn
  per the recorded contract.
* TypeScript: connection tests for the ext call + notifications; model tests for
  queued→delivered styling flip, interrupted-marker decision table, autoscroll
  stickiness predicate; App test for mid-turn submit posting `enqueueMessage` and the
  capability-absent fallback (input disabled).

## Checkpoint criteria

* Both subprojects green; full manual pass of the end-to-end feature matrix (streaming,
  tools, approvals, questions, controls, tasks, queueing, cancel) — script the matrix
  as a checklist in the PR description.
* Specs updated both sides; overview plan's feature-parity claims re-verified against
  reality (any gap found gets a TODO.md entry under a `### Plan 016` section per the
  plans README, or a follow-up increment doc).
