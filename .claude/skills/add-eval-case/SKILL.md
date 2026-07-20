---
name: add-eval-case
description: Add a tool-efficacy EvalCase to klorb/evals/cases.py. Use when the user wants to add "evals" or "eval cases" for a klorb tool, cover a new tool's usability under klorb/evals/, or asks for eval coverage of a scenario (e.g. "add evals for ListDir", "write an eval case for the new tool"). Distinct from klorb/tests/ unit tests, which check apply() logic against mocked-up arguments, not whether a real model can figure out how to drive the tool.
---

# Adding a tool eval case to klorb/evals/

Evals (`klorb/evals/`) answer a different question than `klorb/tests/`: not "does this tool's
`apply()` behave correctly given specific arguments" (that's a unit test), but "can a real model,
given only the tool's real `name()`/`description()`/`parameters()`, actually figure out how to
call it correctly to accomplish a task." A tool can have flawless `apply()` logic and still fail
here if its schema or description is ambiguous or under-specified. Read
`docs/specs/tool-eval-harness.md` and the ADRs it links (`grade-tool-evals-by-filesystem-state`,
`eval-conditional-pass-on-excess-tool-calls`, `tool-evals-skip-without-api-key`,
`reuse-session-for-tool-eval-agent-loop`) before writing a case if anything below is unclear —
this skill summarizes them, it doesn't replace them.

Evals make real network calls to a real hosted model and are **not** part of `make test` — they
run under `make evals` and are skipped (not failed) if `OPENROUTER_API_KEY` isn't set. That means
you can't rely on running the eval itself to check your work; lint/typecheck plus careful reading
of your `check()` logic against the setup files is the actual verification step.

## 1. Design the scenario around a hidden ground truth

The single most important property of a good eval case: **the correct answer must not be
guessable without the model genuinely using the tool correctly.** If a model could pass by luck,
pattern-matching on the prompt, or recalling training data, the case measures nothing. Concretely:

* Vary or randomize the setup content (file names, counts, which of several subdirectories holds
  a marker file, etc.) so nothing about the correct answer is inferable from the prompt text
  alone — the model has to actually call the tool and read the result.
* Prefer scenarios where the tool's return value carries information the model has no other way
  to obtain (a file's actual line count, a directory's actual contents, a search hit's actual
  line number).

## 2. Write the case

In `klorb/evals/cases.py`, follow the existing shape for every case:

1. A private `_check_<name>(workspace_root: Path, session: Session) -> str | None` function:
   returns `None` on success, or a human-readable failure string (include both expected and
   actual values in the message — this is what shows up in the eval report and `evals.log`).
2. An `EvalCase(...)` constant in `UPPER_SNAKE_CASE` matching `_name`, with:
   * `name`: short snake_case identifier, unique across `FILE_TOOLS_CASES`.
   * `prompt`: the exact user message sent to the model. State the task precisely enough that a
     competent model has one obvious correct interpretation — ambiguity in the prompt is not the
     thing this harness is trying to measure.
   * `setup_files`: a `dict[str, str]` of workspace-relative path -> initial content, written
     before the prompt is sent. Keep it minimal — only what the prompt actually depends on.
     Nested paths (e.g. `"data/a.txt"`) create their parent directories automatically; there is
     no way to seed an *empty* directory this way (only directories that hold at least one seeded
     file), so a scenario needing an empty subdirectory has to have the model create it.
   * `check`: the function from step 1.
   * `expected_tool_calls`: the number of tool calls a clean, competent run should need (e.g. one
     `ListDir` plus one `CreateFile`). This isn't a hard pass/fail gate — a case that passes but
     exceeds it is flagged `CONDITIONAL PASS` (yellow), a signal the tool's schema/description
     likely confused the model into retries even though it eventually recovered. Leave it `None`
     only if there's genuinely no meaningful budget to check against.
3. Append the new constant to the `FILE_TOOLS_CASES` list at the bottom of the file, in the same
   relative position as the tool it covers (keep tool-specific cases grouped together). That list
   backs the `"file-tools"` `EvalSuite` (`klorb.evals.harness.EvalSuite`) -- only start a new
   suite in `ALL_SUITES` for a scenario group that genuinely doesn't belong under `file-tools`.
4. If this is the first case for a tool not yet mentioned there, update `cases.py`'s module
   docstring and `docs/specs/tool-eval-harness.md`'s "Out of scope" bullet to list it.

## 3. Grade on filesystem state, never on `final_response`

`check()` must assert on real on-disk file content (`Path.read_text()`/`.exists()`/etc.) under
`workspace_root`, and *never* on the model's closing text — see
`docs/adrs/grade-tool-evals-by-filesystem-state.md`. There is no LLM-as-judge grading anywhere in
this harness; don't introduce fuzzy/semantic matching.

Two helpers already in `cases.py` cover almost every case's needs:

* `_first_mismatch(expected: list[str], actual: list[str], filename: str) -> str | None` — compare
  two line lists and describe the first difference, for files too long to usefully dump in full
  on failure.
* `_tool_call_args(session: Session, tool_name: str) -> list[dict]` — every call to `tool_name`
  the model made, as parsed argument dicts, in call order. Use this (in addition to, never
  instead of, a filesystem check) only when the filesystem outcome alone can't distinguish a
  genuinely correct approach from a lucky one — e.g. the `EditFile` ambiguous-match cases check
  that the model actually supplied `context_before`/`context_after` to disambiguate, not just that
  the file ended up right.

## 4. Verify

Run `make -C klorb lint typecheck test` (see `CLAUDE.md`'s bash rules — a single command, don't
chain with `cd`). This won't execute your new case (no API key in CI), but it does catch syntax
errors, type errors, and unused-import/naming issues in `cases.py`. Read back through your
`check()` function and `setup_files` once more against the "hidden ground truth" requirement in
step 1 — that property can't be caught by any automated check.

If the user has an `OPENROUTER_API_KEY` configured and wants real confirmation, `make evals`
(from `klorb/`) runs every known suite against a real model and prints a report (`EVALARGS=
'--suite file-tools' make evals` to run just this one); a single new case can also be exercised
directly via `klorb.evals.harness.run_case()` in a scratch script if faster iteration is needed.

## Worked example

`klorb/evals/cases.py`'s `ListDir` cases are a complete worked example of this pattern:

* `LIST_DIR_ROOT_INVENTORY` seeds a mix of top-level files and subdirectories in a deliberately
  non-alphabetical creation order, and requires the model to reproduce them sorted — the sort
  order and exact name set can't be guessed, only observed via a real `ListDir` call.
* `LIST_DIR_COUNT_FILES_IN_SUBDIRECTORY` seeds an arbitrary number of files in `data/` and asks
  for an exact count — unguessable without listing the directory.
* `LIST_DIR_FIND_SUBDIRECTORY_WITH_MARKER` seeds several sibling subdirectories where exactly one
  contains a marker file, requiring the model to enumerate `reports/` first, then check each
  child — a scenario that specifically exercises `ListDir` at more than one directory depth.
