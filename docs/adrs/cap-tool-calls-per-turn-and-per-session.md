# Cap individual tool calls per turn (5) and per session (25), in addition to the round-trip cap

* Date: 2026-07-01 17:40
* Question: `MAX_TOOL_CALL_ROUNDS` (see
  [the tool-calling wiring ADR](wire-tool-calling-into-the-session-turn-loop.md)) already
  caps how many model-to-tool round trips one turn will run. But a single round can request
  several parallel tool calls at once (`Message.tool_calls` is a list), so the round cap
  alone doesn't bound how many individual tool invocations — each with its own cost, latency,
  and side-effect risk — one turn, or one session's worth of turns, can rack up. Should
  `_run_tool_calls` also cap the raw count of individual calls it executes, separately from
  the round cap, and if so, at what scope(s) and what defaults?
* Answer: `_run_tool_calls` enforces two additional caps, both checked (and logged) before
  every individual call, not just once per round or once per turn: `max_tool_calls_per_turn`
  (default `DEFAULT_MAX_TOOL_CALLS_PER_TURN = 5`, `Session._tool_calls_this_turn`, reset to
  `0` at the top of every `_dispatch_turn()` call including retries) and
  `max_tool_calls_per_session` (default `DEFAULT_MAX_TOOL_CALLS_PER_SESSION = 25`,
  `Session._tool_calls_this_session`, never reset — accumulates for the `Session`'s entire
  lifetime across every turn). Both defaults are duplicated as `ProcessConfig` fields
  (`max_tool_calls_per_turn`/`max_tool_calls_per_session`, on-disk keys
  `tools.maxCallsPerTurn`/`tools.maxCallsPerSession`), configurable the same way as
  `read_file_max_lines`, and threaded into `Session`'s constructor by `cli.py`/`repl.py`.
  Exceeding either raises `ToolCallLimitExceeded` — the same exception `MAX_TOOL_CALL_ROUNDS`
  raises — without executing the call that would have exceeded it (or any later call in that
  round's batch); calls already dispatched earlier in the same round remain in history.
* Reasoning: The round cap bounds *conversation depth* (how many times the model gets to react
  to tool output before answering), but a model requesting many parallel calls in one round
  sidesteps that entirely — five rounds of ten parallel calls each is 50 tool invocations under
  a round cap of 10, an unbounded-feeling number for what's meant to be a safety valve. A
  call-count cap closes that gap directly. Two scopes (turn and session) exist because they
  guard against different failure modes: `max_tool_calls_per_turn` bounds the cost of any
  single user request (protects against one bad prompt or one confused model turn), while
  `max_tool_calls_per_session` bounds the cumulative cost of a long-running REPL session
  (protects against many turns each individually under the per-turn cap adding up over time).
  Checking before *every* call rather than only between rounds means a batch of parallel
  calls is cut off precisely at the limit rather than allowed to overrun it by up to a whole
  round's worth of calls. The check-then-raise (not check-then-truncate-and-continue) design
  matches `MAX_TOOL_CALL_ROUNDS`'s existing behavior: hitting a cap fails the whole turn
  (`user_message` marked `processing_state="error"`) rather than silently returning a partial
  answer, so the failure is visible rather than masked. Defaults of 5 and 25 are deliberately
  small starting points for a feature with no production usage data yet; both are
  process-config-overridable so they can be tuned without a code change once real usage
  patterns are known.
