# 00209: Eval cases close their session, cap tool calls, and enforce a 120s timeout

**Date:** 2026-08-26

**Question:** A stray `WakeUpTimer` call in one eval case scheduled a `threading.Timer` that
outlived that case's own `Session`, then fired mid-setup of the next case and raised
`ChainedHookMessageUndeliverableError` on stderr — because `run_case()` never called
`session.close()`, so nothing ever ran `TimerScheduler.close()`'s teardown. How should
`klorb/evals/harness.py` isolate each case so one case's leftover state (a live timer, a runaway
model stuck in a tool-call loop, a hung request) can't corrupt the next case or hang the suite?

**Answer:**

* `run_case()` now calls `session.close()` in a `finally` block once a case's turn and grading
  are done, so every `register_teardown` callback (`TimerScheduler.close()` among them) always
  runs before the next case's temp workspace is built.
* Every case's `send_turn()` is wrapped in a wall-clock timeout: a `threading.Timer` sets a
  `cancel_event` after 120 seconds, passed to `send_turn()` via `TurnEventHandlers`. A case still
  running past that point is aborted and fails with a timeout `error`.
* `SessionConfig.max_tool_calls_per_turn` is set per case (default 20; twice
  `EvalCase.expected_tool_calls` for a case whose expected count exceeds 12) so a model stuck
  retapproving the same call fails fast instead of burning the full 120s budget.
* `CreateSubagent` and `WakeUpTimer` are removed from every case's `ToolRegistry` by default,
  since both can leave background work (a subagent process, a timer) running past a case's own
  teardown. `EvalCase.allow_tool_names` re-enables a specific name for a case that genuinely
  exercises it — `subagent_cases.py`'s case sets `allow_tool_names=frozenset({"CreateSubagent"})`.

**Reasoning:** The teardown gap was a real bug, not a hypothetical: it produced the exact stderr
traceback that prompted this change. The tool-call cap and timeout close the same class of gap
for two failure modes `close()` alone doesn't fix — a model that never issues a final answer, or
one that's simply slow — either of which would otherwise hang the whole suite rather than failing
just the one case. Disabling `CreateSubagent`/`WakeUpTimer` by default removes two ways a case can
spawn something with a lifetime longer than the case's own `Session`, while the opt-in field keeps
the one existing suite that legitimately needs `CreateSubagent` working.
