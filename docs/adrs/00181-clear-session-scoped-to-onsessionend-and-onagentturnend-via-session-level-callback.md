# `HookOutput.clear_session` only fires from `onSessionEnd`/`onAgentTurnEnd`, via a `Session`-level callback

* Date: 2026-08-09
* Question: A hook/event author wants to discard the current session and start a fresh one seeded
  with a message, as if the user had run "Clear session" — e.g. a classifier that decides the
  conversation has drifted onto an unrelated topic. `HookOutput` is the shared output shape every
  hook and event type returns, so a `clear_session` field on it is visible to all of them
  (`onToolUse`, `onSubagentStart`, `FileSystemModified`, ...). Two questions this raises: which of
  those call sites should actually act on it, and how does `Session` — which has no reference to
  whatever TUI/ACP object is holding it as "the live session" — make the replacement happen at all?
* Answer: Only two call sites read `clear_session`: `SessionTurnsMixin._fire_agent_turn_end_hook`
  (`onAgentTurnEnd`) and `SessionCoreMixin.close()` (`onSessionEnd`). Every other hook/event may set
  the field — `HookDispatcher` folds and validates it the same way regardless of which hook fired —
  but nothing consumes it there. `Session` never replaces itself: it exposes
  `on_clear_session_requested: Callable[[str], None] | None`, a plain attribute a host (`klorb.tui.
  ReplApp`) registers on every `Session` it ever holds; `close()`/`_fire_agent_turn_end_hook` invoke
  it with the aggregate `message` instead of acting themselves. `None` (no host registered) degrades
  to a logged warning rather than an error — `onAgentTurnEnd` additionally falls back to an ordinary
  `start_turn_or_enqueue(message)` chained turn, the same delivery a `clear_session`-less `message`
  already gets.
* Reasoning: Restricting *where* `clear_session` is actionable to these two hooks keeps the feature
  narrow enough to reason about without new turn-cancellation machinery: both fire only when no turn
  is in flight on the firing session (`onAgentTurnEnd` after `_dispatch_turn`'s own `finally` already
  cleared turn state; `onSessionEnd` at session teardown), so `clear_session` never has to reconcile
  with an actively-streaming response the way `interrupt` was designed to. A broader scope (e.g.
  `onToolUse` mid-turn) would need to decide whether to forcibly cancel that turn — deliberately out
  of scope here. The callback, not a `Session` method that mutates some global "current session"
  registry, follows `AGENTS.md`'s "keep agent functionality reachable from `Session`; don't pollute
  it with TUI-/ACP-specific connection — use a callback instead": `Session` has no business knowing
  whether it's held by a TUI's `self._session`, an ACP agent's single-session slot, or a headless
  one-shot local variable, only that *something* might want to know when a replacement is requested.
  `ReplApp` binds the same callback (`_replace_session`) that its own "Clear session" palette command
  already uses, reusing one code path rather than a second session-construction routine — see
  `docs/specs/hooks-and-events.md`'s "Session replacement" section for how it further guards against
  the callback re-entering itself (an `onSessionEnd` handler firing from inside the very
  `Session.close()` call a replacement is already making) and marshals across the worker-thread
  boundary an `onAgentTurnEnd` firing can arrive from.
