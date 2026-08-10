# `HookOutput.reset_session` mutates the firing `Session` in place instead of replacing it

* Date: 2026-08-09
* Question: `HookOutput.clear_session` (see
  docs/adrs/00181-clear-session-scoped-to-onsessionend-and-onagentturnend-via-session-level-callback.md)
  discarded the firing `Session` and built a brand new one, handed back to whichever host owned
  it via a `Session.on_clear_session_requested` callback. That design turned out to be the most
  convoluted piece of the hooks/events feature: a cross-thread `call_from_thread` marshal (since
  an `onAgentTurnEnd`-triggered request arrives from `_send_prompt`'s worker thread, unlike a
  manual `/clear`), a reentrancy guard against the replacement's own `Session.close()` re-firing
  `onSessionEnd`, host-specific callback wiring only the TUI ever implemented, and a config
  reload-from-disk-plus-reapply-CLI-flags dance. Could the same "start this conversation over"
  effect be achieved without replacing the `Session` object at all?
* Answer: Renamed the field `reset_session` and changed its effect: `Session.reset_session(message)`
  wipes the firing session's own state in place — `config` reinitialized from `ProcessConfig.
  session`'s template, `_messages`/counters/one-shot-interjection flags/scratchpad/persistent bash
  shell reset via a new `Session._reset_state()` shared with `__init__` itself (so construction and
  reset can never drift apart) — then starts `message` as the next turn via the existing
  `start_turn_or_enqueue`. Identity (`id`/`root_id`/`parent`) and persistence identity
  (`_session_lock`/`_session_subdir`) are left untouched: it's the same session, same on-disk
  directory, just wiped. `on_clear_session_requested` and all TUI-side wiring for it
  (`_bind_clear_session_handler`) are removed outright; the "Clear session" palette command keeps
  its own separate, heavier `_replace_session` (a real new session, new id, config reread from
  disk) unchanged. `close()`'s `onSessionEnd` handling now aborts the shutdown entirely when
  `reset_session` is set, calling `reset_session()` instead of running its usual
  cascade-close/persist/teardown sequence. An `onAgentTurnEnd`-triggered reset additionally
  dispatches `onSessionEnd` with `event="ResetSession"` first, purely for side effects, so an
  `onSessionEnd` handler sees a reset happen regardless of which hook triggered it.
* Reasoning: Every piece of complexity ADR 00181 accepted existed to solve one problem: telling
  some other object (a TUI app, an ACP agent) that the `Session` it holds a reference to needs to
  be swapped for a different one. Mutating the same object in place instead makes that problem
  disappear rather than solving it — no callback, no host wiring, no object handoff, so nothing
  needs marshaling across threads or guarding against reentrant replacement either. The remaining
  question was whether `reset_session()` could safely run synchronously, mid-call-stack, inside
  `_fire_agent_turn_end_hook` (itself called from `_dispatch_turn`, before `send_turn()` returns to
  its caller): it can, because `_dispatch_turn` has already captured the completed turn's
  `result_text` into a local variable by that point, and `send_turn()` itself does nothing further
  with `self._messages` after `_dispatch_turn()` returns — nothing on that call stack still depends
  on the wiped state. This also incidentally fixes the old design's biggest functional gap for
  free: `KlorbAcpAgent`/headless `klorb.cli.main()` never bound `on_clear_session_requested` (no
  protocol-level way to tell an ACP client its `session_id` now points at a different object), so
  `clear_session` only ever worked in the TUI. `reset_session` needs no such binding, so it works
  identically everywhere.
