# Tool evals are graded by inspecting on-disk file state, not the model's closing text

* Date: 2026-07-02 18:40
* Question: Anthropic's tool-evaluation cookbook grades each eval task by exact string equality
  between the model's final text response and an expected answer (e.g. a calculator's numeric
  result). klorb's file tools (`ReadFile`, `CreateFile`, `EditFile`, `ReplaceAll`) don't produce
  an "answer" in that sense — their effect is a file mutation (or, for `ReadFile`, tool-call
  arguments/results), and the model's closing remark ("I've updated the file.") carries no
  signal about whether the edit was actually correct. How should a `klorb/evals/` `EvalCase` be
  graded?
* Answer: Each `EvalCase` supplies a deterministic `check(workspace_root, session) -> str |
  None` callback, run after `session.send_turn()` returns, that inspects the actual file(s) left
  behind under `workspace_root` (and, where useful, `session.messages` for the tool calls that
  were made) and returns `None` on success or a human-readable failure reason otherwise. No
  LLM-as-judge grading is used anywhere in the harness.
* Reasoning: the cookbook's own writeup flags exact-string grading as brittle — its one failing
  task ($11,614.72" vs "11614.72") failed on formatting, not substance. klorb's file tools sidestep
  that trap entirely: the ground truth of "did the model use `EditFile`/`ReplaceAll`/`CreateFile`
  correctly" is concretely observable as bytes on disk after the turn, independent of how the
  model chooses to phrase its final sentence. Checking file content directly is exact,
  deterministic, requires no second model call (no added cost, latency, or judge-model grading
  noise), and matches the spirit of [[reuse-session-for-tool-eval-agent-loop]]: the harness
  trusts real tool execution results (real disk I/O through the real tools), not a model's
  self-report of what it did.
