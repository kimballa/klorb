# Remove the per-session tool-call cap; only the per-turn cap is useful

* Date: 2026-08-10 16:30
* Question: [[cap-tool-calls-per-turn-and-per-session]] gave `Session` two independent tool-call
  safety caps, `max_tool_calls_per_turn` and `max_tool_calls_per_session`, each interactively
  doubling on approval. In practice the session-scoped cap (`self._tool_calls_this_session`,
  never reset for the life of a `Session`) rarely does anything a user wants: a long-running,
  legitimate interactive session racks up tool calls across many turns as a matter of course, so
  the cap mostly just re-prompts "continue?" on an already-productive session, with no bearing on
  whether any single turn is a runaway loop. Should the per-session cap be removed, keeping only
  the per-turn one?
* Answer: Yes. `SessionConfig.max_tool_calls_per_session`, `DEFAULT_MAX_TOOL_CALLS_PER_SESSION`,
  `Session._tool_calls_this_session`, the `tools.maxCallsPerSession` config key, and the
  `--max-tool-calls-per-session` CLI flag are all removed outright — not deprecated or defaulted
  to "unlimited". `_confirm_limit_increase()` drops its `scope` parameter entirely, since
  `max_tool_calls_per_turn` is now the only limit it ever raises. `MAX_TOOL_CALL_ROUNDS` (the
  hard, non-raisable per-turn round-trip cap) and `max_tool_calls_per_turn` (the raisable
  per-turn call-count cap) are untouched.

  `DEFAULT_MAX_TOOL_CALLS_PER_TURN` moves from `50` to `100`, matching the packaged
  `default-config.json`'s own `tools.maxCallsPerTurn` value (previously out of sync with the
  code constant). This repository's own `.klorb/klorb-config.json` sets
  `sessionDefaults["tools.maxCallsPerTurn"]` to `150` for developing klorb itself, where turns
  routinely run long tool-call chains (test suites, lint fixes, multi-file refactors).
* Reasoning: The per-turn cap bounds the cost of any single request — a real, useful brake on a
  model that gets stuck looping within one turn. The per-session cap bounds cumulative cost
  across a `Session`'s entire lifetime, but a session's lifetime has no natural relationship to
  how much legitimate work it should do: an interactive REPL session might run for hours and
  correctly make thousands of tool calls across many turns, so the cap's only practical effect is
  a stream of "continue?" prompts that don't correlate with anything going wrong. Removing it
  outright, rather than raising its default sky-high, avoids carrying dead machinery (a second
  counter, a second config key, a second CLI flag, `scope="session"` branching) for a limit that
  was never earning its complexity.
