# Tool eval harness

## Summary

`klorb/evals/` is a small harness that measures *tool efficacy*: given a tool's real
`name()`/`description()`/`parameters()`, can a real model actually figure out how to call it
correctly to accomplish a task? This is a different question from what `klorb/tests/` answers
(does the tool's `apply()` logic behave correctly given specific arguments) — a tool can have a
perfectly correct implementation and still be hard for a model to drive if its schema or
description is ambiguous, under-specified, or surprising. The harness's design follows the
methodology in Anthropic's tool-evaluation cookbook (deterministic grading, per-task tool-call
metrics, a rendered report) adapted to klorb's own `Session`/`ToolRegistry` machinery — see
[the reuse-Session ADR](../adrs/reuse-session-for-tool-eval-agent-loop.md) and
[the filesystem-grading ADR](../adrs/grade-tool-evals-by-filesystem-state.md).

Evals are **not** part of `make test` / `klorb/tests/`: they make real network calls to a real
hosted model (cost, latency, third-party nondeterminism) rather than exercising code paths
offline against mocks. They run under their own `make evals` target instead — see
[the skip-without-a-key ADR](../adrs/tool-evals-skip-without-api-key.md) for why a missing
`OPENROUTER_API_KEY` skips rather than fails that target.

## How it works

* `klorb/evals/harness.py` defines:
  * `EvalCase` — a frozen dataclass describing one eval task: `name`, `prompt` (the user
    message sent to the model), `setup_files` (a `dict[str, str]` of workspace-relative paths
    to their initial content, written before the prompt is sent), `check` — a
    `Callable[[Path, Session], str | None]` invoked with the case's temp `workspace_root` and
    the `Session` that ran it, returning `None` on success or a human-readable failure reason —
    `expected_tool_calls` (`int | None`, default `None`): the number of tool calls a
    competent, error-free run should need, used only to flag an otherwise-passing case as a
    `CaseResult.conditional` pass (see [[eval-conditional-pass-on-excess-tool-calls]]) — and
    `soft_check` (same signature as `check`, default `None`): run only when `check` itself
    passes, for a case whose file-state outcome is correct but whose *shape* (which tool-call
    form the model reached for) is a yellow flag rather than a failure — e.g. proving a long-span
    `EditFile` call used `start_text`/`end_text` rather than a token-wasteful `old_text` (see
    docs/specs/tool-framework.md). A non-`None` `soft_check` return also flags the
    result `CaseResult.conditional`, via `CaseResult.soft_failure_reason`, without flipping
    `passed` to `False`.
  * `CaseResult` — the outcome of running one `EvalCase`: `passed`, `duration_s`,
    `num_tool_calls`, `tool_call_counts` (`dict[str, int]`, per tool name), `tool_call_log`
    (`list[ToolCallLogEntry]` — the ordered, raw request/response transcript: each entry's
    `name`/`arguments` is a model tool call exactly as sent, `response` is the matching
    `role="tool_response"` message's `content`), `failure_reason`, `error` (set instead of
    running `check` at all if `send_turn()` itself raised), `final_response` (the model's
    closing text, kept for the report only — never used for grading), `expected_tool_calls`
    (copied from the case), `soft_failure_reason` (set when `EvalCase.soft_check` ran and
    reported a problem), `generated_tokens` (an informational, non-graded `tiktoken` estimate of
    how many tokens this case's run generated — its tool-call `arguments` strings plus
    `final_response` — under the eval model's encoding; the closest proxy this harness has to
    cost without real token accounting), and the `conditional` property — true when `passed` is
    true but either `num_tool_calls > expected_tool_calls` or `soft_failure_reason` is set — see
    [[eval-conditional-pass-on-excess-tool-calls]].
  * `run_case(case, *, model, provider) -> CaseResult` — the per-case runner. For each case it:
    1. Creates a fresh temp directory and writes `setup_files` into it.
    2. Builds a `SessionConfig(model=model, interactive=False, thinking_enabled=False,
       workspace=Workspace(path=<temp dir>), read_dirs=DirRules(allow=[<temp dir>]),
       write_dirs=DirRules(allow=[<temp dir>]))` — explicit `allow` rules for the temp
       workspace in both tables, since an unmatched `writeDirs` table normalizes to `"ask"`
       rather than `"allow"` (see
       docs/adrs/write-verdict-is-stricter-of-read-and-write-tables.md) and this harness runs
       non-interactively, where `"ask"` has no prompting flow to resolve and fails closed.
    3. Builds a `ToolRegistry(ProcessConfig(), session_config, package=klorb.tools)` — the real
       tools package, so exactly the tools a live session would offer (`ReadFile`, `CreateFile`,
       `EditFile`, `ReplaceAll`; see docs/specs/tool-framework.md) are offered here too.
    4. Constructs a `Session` from those and calls `session.send_turn(case.prompt)` — see
       [[reuse-session-for-tool-eval-agent-loop]] for why this drives the real turn loop instead
       of a bespoke one.
    5. Reads tool-call metrics back off `session.messages` (every `role="tool_use"` message's
       `tool_calls`), then runs `case.check(workspace_root, session)` (skipped, and `error` set
       instead, if `send_turn()` raised); if `check` passed and `case.soft_check` is set, runs
       that too.
    6. Estimates `generated_tokens` from `final_response` plus every tool call's raw `arguments`
       string, under `model`'s `tiktoken` encoding (the same one `tool_token_counts()` below
       uses).
  * `run_evaluation(cases, *, model, provider) -> list[CaseResult]` — runs every case in
    sequence (cases are cheap, and sequential order keeps a failing case's model output easy to
    correlate with its position in the printed report) and returns their results.
  * `tool_token_counts(*, model) -> dict[str, int]` — independent of any `EvalCase` (the tool
    package's tools and schemas never vary by case), this builds one throwaway `ToolRegistry`
    (default `SessionConfig()`; no I/O happens building tool definitions, so no real workspace
    is needed) and, for each discovered tool, JSON-encodes the same `{"type": "function",
    "function": {"name", "description", "parameters"}}` dict `ToolRegistry.tool_definitions()`
    builds and counts its tokens with a `tiktoken` encoding resolved for `model` (falling back
    to GPT-4o's `o200k_base` for any non-OpenAI model proxied through OpenRouter, which
    `tiktoken` can't map directly — an approximation in that case, not that model's real
    tokenizer). This is the fixed per-turn prompt cost of offering a tool, as distinct from
    `CaseResult`'s per-case tool-call metrics.
* `klorb/evals/cases.py` holds the synthesized `EvalCase` list covering `ReadFile`, `CreateFile`,
  `EditFile`, `ReplaceAll`, `ListDir`, and the skill tools (`ActivateSkill`/`ReadSkillFile`) — see
  its module docstring for the full list of scenarios.
* `klorb/evals/report.py` renders a `list[CaseResult]` (plus an optional `tool_token_counts`) as
  a markdown report: `render_summary()`'s block (pass count, conditional-pass count, total
  duration, total/average tool calls), an optional "Tool definitions" section (each tool's name
  and its `tool_token_counts()` value, largest first) when `tool_token_counts` is given, then one
  section per case (prompt, `status_label()`'s `PASS`/`CONDITIONAL PASS`/`FAIL`, duration, tool
  calls made vs. expected, `generated_tokens`, failure/soft-check reason or error if any).
  `status_label(result, color=...)` is the single place that turns a `CaseResult` into its
  three-way status string (green `PASS`, yellow `CONDITIONAL PASS` — either over budget on tool
  calls or `soft_failure_reason` set, red `FAIL`) — shared with `run_evals.py`'s live per-case
  progress printer so both agree on the same status for the same result. `render_case_detail(result,
  color=...)` renders one case's tool-call transcript (`  -> Tool(args)` / `  <- response`, red
  when the response starts with `"Error:"`) plus its tool-call counts, `generated_tokens`,
  optional soft-check reason, and status line — the same block `render_report()`'s per-case
  section and `run_evals.py`'s live progress printer both show, factored out so `run_evals.py`
  can also write it (with `color=False`) to `evals.log`.
* `klorb/evals/colors.py` is a minimal ANSI-color helper: `use_color(stream)` reports whether
  `stream.isatty()`, and `colorize(text, color, enabled=...)` wraps `text` in ANSI codes only
  when `enabled` — callers decide and pass that decision in explicitly, so nothing in this
  module inspects a stream on its own and output redirected to a file stays plain text.
* `klorb/evals/run_evals.py` is the entry point `make evals` invokes: calls `load_dotenv()`
  first (no path argument, so `python-dotenv` walks up from the current working directory —
  `klorb/`, since that's where `make evals` runs — and finds the repo-root `.env` one level up
  if present, same as [[openrouter-prompt-client]]'s one-shot CLI), then checks for
  `OPENROUTER_API_KEY` (skip-not-fail if still absent after that — see
  [[tool-evals-skip-without-api-key]]), builds an `OpenRouterApiProvider`, runs every case from
  `klorb.evals.cases.CASES`, prints the rendered report — passing `render_report()` the result
  of `tool_token_counts(model=model)` so the report's "Tool definitions" section reflects the
  model actually under test — and exits `1` if any case failed (`0` otherwise) so `make evals`
  fails CI-visibly when it *does* run with a key configured.
  The model defaults to `klorb.openrouter.DEFAULT_MODEL`, overridable with `--model` or the
  `KLORB_EVAL_MODEL` environment variable, so eval runs stay cheap by default but can be pointed
  at a specific model under investigation.
  * It passes `run_evaluation()` an `on_case_start` callback (prints `"<name>..."` as each case
    begins) and an `on_case_complete` callback (prints `tool_call_log`'s raw request/response
    transcript — `  -> Tool(args)` / `  <- response`, color-coded red when the response starts
    with `"Error:"` — followed by a `[PASS]`/`[FAIL]` line), so a long `make evals` run shows
    what's actually happening case by case instead of going silent until the final report.
    `use_color()` (checked once, against `sys.stdout`) decides whether any of that output — the
    live progress lines and the final report's `[PASS]`/`[FAIL]` markers — is colorized.
  * After every case has run, `_write_eval_log()` writes `evals.log` in the current directory
    (`klorb/`, since that's where `make evals` runs) as a plain, non-colorized record of the run:
    `render_case_detail(result, color=False)` for every case that failed or conditionally passed
    (see [[eval-conditional-pass-on-excess-tool-calls]]), followed by `render_summary()`'s block.
    A fully clean run (every case a plain `PASS`) has no per-case sections worth re-reading, so
    `evals.log` then holds just the summary block. This file isn't committed (see the repo-root
    `.gitignore`'s `evals.log` entry) — it's a scrollback aid for the run that just happened, not
    build output.
  * `--self-review` (off by default) closes the loop after `evals.log` is written:
    `_run_self_review()` mints a scratch temp directory via
    `klorb.permissions.directory_access.create_tempdir_for_session(session_config,
    remove_on_exit=True)`, copies `evals.log` into it, and sends a fresh, non-interactive
    `Session` on the same model/provider a prompt that names the copied log's path, reminds it to
    `ReadFile` the path, explains the log's three-way grading vocabulary (so `CONDITIONAL PASS`
    reads as a yellow flag, not pass-or-fail), and asks it to diagnose which *tools'* ergonomics
    (not only `EditFile`) caused failures or retries and propose up to five prioritized, concrete
    fixes. The review session's `workspace.trusted=True` so the `readDirs.allow` grant on the
    scratch directory actually lifts the read boundary onto it (see
    `resolve_and_evaluate_read()`'s trusted-mode branch). The diagnosis is printed to the
    terminal and appended to `evals.log` as a final `## Self-review` section (`_write_eval_log()`
    re-run with `self_review=<text>`), so it reaches both outputs identically rather than only
    the terminal. It does not affect `main()`'s exit code. When `--self-review` isn't passed, the
    run prints a one-line reminder that it's available.

## Adding a new eval case

Append an `EvalCase` to `klorb/evals/cases.py`'s `CASES` list. `check` should assert on the
resulting workspace file state (read the file back with `pathlib.Path.read_text()`) rather than
on `final_response` text — see [[grade-tool-evals-by-filesystem-state]]. Keep `setup_files`
minimal: only what the case's prompt actually depends on. Set `expected_tool_calls` to the
number of calls a clean run needs (a `ReadFile` to see current content, plus one `EditFile` or
`ReplaceAll` to change it, is the common shape) so a case that only passes after retries gets
flagged `CONDITIONAL PASS` instead of blending in with a clean `PASS` — see
[[eval-conditional-pass-on-excess-tool-calls]]. If a case's whole point is proving a *specific*
call shape is reachable or preferred (not just that the file ends up correct), inspect
`session.messages` for the argument shape actually used — either as a hard `check` failure (the
shape *is* the pass/fail bar, as the `edit_file_ambiguous_match_*` and
`edit_file_single_line_shortcut`/`edit_file_old_text_*` cases do) or as a `soft_check` when a
different, correct-but-suboptimal shape should downgrade to `CONDITIONAL PASS` rather than fail
outright.

## Out of scope

* The file tools (`ReadFile`, `CreateFile`, `EditFile`, `ReplaceAll`), `ListDir`, and the skill
  tools (`ActivateSkill`/`ReadSkillFile`) are covered today; the harness itself is tool-agnostic
  (any `klorb.tools` package works) and can grow to cover future tools by adding more `EvalCase`s.
  An `EvalCase` may set `workspace_trusted`/`skill_rules` for a scenario (e.g. a skill case) that
  needs a trusted workspace or a pre-`allow`ed skill.
* No LLM-as-judge grading, and no fuzzy/semantic matching of `final_response` — see
  [[grade-tool-evals-by-filesystem-state]].
* Not wired into `make test` or CI's default gate — see [[tool-evals-skip-without-api-key]].
