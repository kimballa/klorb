# Raise tool-call limit defaults to 50/turn, 200/session, 200 rounds

* Date: 2026-07-02 00:00
* Question: [The original tool-call caps
  ADR](cap-tool-calls-per-turn-and-per-session.md) set `DEFAULT_MAX_TOOL_CALLS_PER_TURN = 5`,
  `DEFAULT_MAX_TOOL_CALLS_PER_SESSION = 25`, and (in [the tool-calling wiring
  ADR](wire-tool-calling-into-the-session-turn-loop.md)) the hard, non-raisable
  `MAX_TOOL_CALL_ROUNDS = 10`. In practice these caps proved too tight for legitimate
  agentic tasks — even routine multi-file work routinely hit the per-turn prompt before
  finishing. Should the defaults be raised, and to what?
* Answer: Raise all three: `DEFAULT_MAX_TOOL_CALLS_PER_TURN` 5 → 50,
  `DEFAULT_MAX_TOOL_CALLS_PER_SESSION` 25 → 200, and `MAX_TOOL_CALL_ROUNDS` 10 → 200
  (`klorb/src/klorb/session.py`). Also updated `etc/klorb-config.json` (the reference
  file documenting every recognized on-disk key at its current default) and the
  `docs/specs/process-and-session-config.md`/`docs/specs/session-and-turns.md` passages
  that cite the old numbers. The interactive doubling behavior, the two-scope split
  (turn vs. session), and the hard/hard-vs-raisable distinction between
  `MAX_TOOL_CALL_ROUNDS` and the two `SessionConfig` caps are unchanged — only the
  starting numbers moved.
* Reasoning: The original defaults were sized as a conservative starting point for a
  brand-new safety mechanism, not calibrated against real task shapes. Once in use, a
  cap of 5 individual tool calls per turn meant the user was prompted to double the
  limit on almost every nontrivial request, turning a safety valve meant to catch
  runaway loops into routine interactive friction. Raising the per-turn/per-session
  defaults tenfold each still preserves a checkpoint for genuinely runaway behavior (a
  turn or session that blows through 50 or 200 calls is a much stronger runaway signal
  than one that blows through 5 or 25), while letting ordinary agentic work proceed
  without repeated interruption. `MAX_TOOL_CALL_ROUNDS` moved from 10 to 200 to match:
  it's the *only* one of the three that isn't interactively raisable, so leaving it at
  10 would have made it the binding (and silently un-raisable) ceiling well before a
  user ever got asked about the per-turn/per-session caps — defeating the point of
  raising those. 200 keeps it a hard backstop against a model stuck in an infinite
  tool-call loop while no longer being tighter than the caps it's meant to backstop.
