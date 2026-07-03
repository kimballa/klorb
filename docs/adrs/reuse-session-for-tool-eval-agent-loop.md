# The tool eval harness drives Session directly instead of reimplementing a chat/tool loop

* Date: 2026-07-02 18:40
* Question: A tool-efficacy eval harness needs to send a prompt to a real model with tool
  definitions attached, dispatch whatever tool calls the model requests, and feed the results
  back until the model returns a final answer — the same shape of loop Anthropic's
  tool-evaluation cookbook hand-rolls as `agent_loop()`. klorb already has exactly this loop in
  `klorb.session.Session._dispatch_turn`/`_run_tool_calls`. Should `klorb/evals/` write its own
  copy of that loop (as the cookbook does, dispatching tool calls via `eval(f"{name}(...)")`),
  or drive a real `Session`?
* Answer: The harness constructs a real `klorb.session.Session` per eval case — a real
  `OpenRouterApiProvider`, a `klorb.tools.registry.ToolRegistry` scanning the actual
  `klorb.tools` package, and a `SessionConfig` pointed at a fresh per-case temp directory as
  `workspace_root` — and drives it with a single `session.send_turn(case.prompt)` call. Metrics
  (tool calls made, per-tool counts) are read back off `session.messages` afterward rather than
  tracked by hand during dispatch.
* Reasoning: `Session._run_tool_calls` already does the round-trip loop, dispatches each call
  through `ToolRegistry.instantiate_tool(name).apply(args)` (a safe name-keyed lookup, not the
  `eval()`-based dispatch the cookbook itself calls out as a security anti-pattern), and enforces
  the same tool-call-limit safety caps a real session would hit. Reimplementing any of that in
  the eval harness would (a) duplicate klorb/src/klorb/session.py's turn loop, which CLAUDE.md's
  "reuse existing API endpoints rather than make new ones" rule and the general
  no-duplicated-logic principle both argue against, and (b) let the eval harness silently drift
  from the real dispatch path a user's session actually takes, so a passing eval wouldn't mean
  what it's supposed to mean. Driving a real `Session` also means the harness is exercising the
  exact same code a live klorb session would run, including any future changes to
  `_dispatch_turn` — no separate loop to keep in sync.
