# Cap individual tool calls per turn (5) and per session (25), interactively raisable, doubling on approval

* Date: 2026-07-01 18:20
* Question: `MAX_TOOL_CALL_ROUNDS` (see
  [the tool-calling wiring ADR](wire-tool-calling-into-the-session-turn-loop.md)) already
  caps how many model-to-tool round trips one turn will run. But a single round can request
  several parallel tool calls at once (`Message.tool_calls` is a list), so the round cap
  alone doesn't bound how many individual tool invocations — each with its own cost, latency,
  and side-effect risk — one turn, or one session's worth of turns, can rack up. Should
  `_run_tool_calls` also cap the raw count of individual calls it executes, separately from
  the round cap? If so, at what scope(s), what defaults, where do those defaults live
  (`ProcessConfig` or `SessionConfig`), and what happens when a long-running, legitimate
  agentic task hits the cap — a hard failure, or something a user can push through?
* Answer: `_run_tool_calls` enforces two additional caps, both checked (and logged) before
  every individual call, not just once per round or once per turn:
  `SessionConfig.max_tool_calls_per_turn` (default `DEFAULT_MAX_TOOL_CALLS_PER_TURN = 5`,
  `Session._tool_calls_this_turn`, reset to `0` at the top of every `_dispatch_turn()` call
  including retries) and `SessionConfig.max_tool_calls_per_session` (default
  `DEFAULT_MAX_TOOL_CALLS_PER_SESSION = 25`, `Session._tool_calls_this_session`, never reset —
  accumulates for the `Session`'s entire lifetime across every turn). Both fields live on
  `SessionConfig`, not `ProcessConfig` — on-disk keys `tools.maxCallsPerTurn`/
  `tools.maxCallsPerSession` inside `sessionDefaults`, per `SESSION_KEY_MAP` — because reaching
  one is meant to be a per-session, user-answerable event (see below), not a fixed process-wide
  ceiling; `Session.__init__` no longer takes them as separate constructor arguments, it reads
  them straight off the `SessionConfig` it's already given.

  Reaching either cap doesn't fail the turn outright. `Session._confirm_limit_increase()` calls
  an optional `on_tool_call_limit_reached: Callable[[str], bool]` callback (threaded through
  `send_turn()`/`retry_last_turn()`/`_dispatch_turn()`/`_run_tool_calls()`) with a
  human-readable prompt. If the callback returns `True`, the reached limit is doubled on
  `self.config` (persisting for the rest of the `Session`'s lifetime, not just this call) and
  the call proceeds; if it returns `False`, or no callback was given at all,
  `ToolCallLimitExceeded` is raised without executing that call (or any later call in the same
  round's batch) — the same exception `MAX_TOOL_CALL_ROUNDS` raises, so it's handled identically
  by `_dispatch_turn`'s existing failure path (`user_message` marked `processing_state="error"`).
  `klorb.tui.repl.ToolCallLimitScreen` is the interactive answer: a `ModalScreen[bool]` with
  Yes/No buttons (Escape = No), shown via `ReplApp._on_tool_call_limit_reached()` —
  `Session.send_turn()` runs on a worker thread, so that callback blocks it with
  `App.call_from_thread(self._confirm_tool_call_limit, message)`, where
  `_confirm_tool_call_limit` is an `async def` that `await`s `push_screen_wait()` on the app's
  own event loop. `cli.py`'s one-shot path passes no callback, so a one-shot prompt that hits a
  cap fails outright — there's no one to ask.
* Reasoning: The round cap bounds *conversation depth* (how many times the model gets to react
  to tool output before answering), but a model requesting many parallel calls in one round
  sidesteps that entirely — five rounds of ten parallel calls each is 50 tool invocations under
  a round cap of 10, an unbounded-feeling number for what's meant to be a safety valve. A
  call-count cap closes that gap directly. Two scopes (turn and session) exist because they
  guard against different failure modes: `max_tool_calls_per_turn` bounds the cost of any
  single user request, while `max_tool_calls_per_session` bounds the cumulative cost of a
  long-running REPL session. Checking before *every* call rather than only between rounds means
  a batch of parallel calls is cut off precisely at the limit rather than allowed to overrun it.

  Making the cap interactively raisable (rather than a hard, fixed ceiling) is the point of the
  whole design: a genuinely long-running agentic task isn't necessarily a runaway one, and a
  hard failure at "5 tool calls" would make the feature useless for real work — the cap exists
  to catch loops and mistakes, not to punish legitimate long tasks. Doubling (rather than, say,
  adding a fixed increment) means the number of times a user has to be asked shrinks as a task's
  real tool-call needs grow, while still eventually re-prompting rather than disabling the limit
  outright — an explicit "no limit" escape hatch wasn't chosen so there's always a checkpoint
  where a truly runaway session gets another chance to be caught. Persisting the raised limit on
  `self.config` (not just for the current call) avoids re-prompting on every single call once a
  user has already said "yes, this task needs more" once. `SessionConfig` (not `ProcessConfig`)
  is the right home specifically because of this interactive-and-mutable-per-session nature —
  `ProcessConfig`'s process-only fields are meant to be identical across every concurrently
  running session (see [[process-and-session-config]]), which doubling-per-session would
  violate. Declining, or having no callback at all (the one-shot CLI case), preserves the
  original hard-cap behavior exactly, so non-interactive callers keep a deterministic ceiling.
