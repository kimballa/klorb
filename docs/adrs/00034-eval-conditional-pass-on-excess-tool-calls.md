# Eval cases get a yellow CONDITIONAL PASS when they pass using more tool calls than expected

* Date: 2026-07-02 19:30
* Question: A tool eval case is graded pass/fail purely by inspecting final on-disk file state
  (see [[grade-tool-evals-by-filesystem-state]]). But a case can reach the right final state
  the hard way — e.g. `EditFile` rejects a call because the model copied `ReadFile`'s `"N|"`
  line-number prefix into `start_text`, the model retries `EditFile` the same broken way a
  couple more times, then gives up and falls back to `ReplaceAll` to actually make the change.
  Binary pass/fail can't distinguish that from a clean one-shot success, so a real tool-usability
  regression (a confusing tool description, an under-specified parameter) can hide behind a
  green `PASS` indefinitely. How should the harness surface this?
* Answer: `EvalCase` gains an optional `expected_tool_calls: int` — the number of tool calls a
  competent, error-free run should need (e.g. one `ReadFile` plus one `EditFile`). `CaseResult`
  carries that value through and exposes `conditional: bool`, true when the case passed but
  `num_tool_calls > expected_tool_calls`. The report and `make evals`'s live progress output
  both render this as a yellow `[CONDITIONAL PASS]` (`klorb.evals.report.status_label`),
  distinct from green `PASS` and red `FAIL`. `CaseResult.passed` — and therefore
  `run_evals.main()`'s exit code — is unaffected: a conditional pass is still a pass, since the
  case's actual grading (filesystem state) didn't change. `expected_tool_calls` is optional and
  defaults to `None` (no threshold checked) so existing/simple cases don't need a value picked
  for them just to keep passing cleanly.
* Reasoning: The point of this harness is to measure tool *usability*, not just tool
  *correctness* (see docs/specs/tool-eval-harness.md) — a model that needs five retries to
  drive a tool correctly has found a real usability bug even if it eventually recovers. Making
  that a hard `FAIL` would be too aggressive: retry counts are somewhat model- and
  prompt-dependent, and a harness that cries wolf on every minor variance trains people to
  ignore red. A third, yellow status keeps the signal visible without blocking CI on a
  correctness metric that didn't actually regress. Keeping `expected_tool_calls` optional avoids
  forcing every case (including ones with no meaningful tool-call budget, like a single
  `CreateFile` call) to carry a value.
