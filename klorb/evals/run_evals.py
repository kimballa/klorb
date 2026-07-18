# © Copyright 2026 Aaron Kimball
"""Entry point for `make evals`: runs klorb.evals.cases.CASES against a real model and prints a
markdown report. See docs/specs/tool-eval-harness.md.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

import klorb.tools as tools_package
from klorb.openrouter import DEFAULT_MODEL, OpenRouterApiProvider
from klorb.permissions.directory_access import create_tempdir_for_session
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.registry import ToolRegistry
from klorb.workspace import Workspace

from .cases import CASES
from .colors import colorize, use_color
from .harness import CaseResult, run_evaluation, tool_token_counts
from .report import render_case_detail, render_report, render_summary

EVAL_MODEL_ENV_VAR = "KLORB_EVAL_MODEL"
EVAL_LOG_PATH = Path("evals.log")

_SELF_REVIEW_PROMPT_TEMPLATE = """\
A klorb tool-efficacy eval suite just finished a run. Its detailed results are saved at \
{log_path} -- use your ReadFile tool to read it (page through it with start_line/end_line if \
it's long).

Each case in the log is graded one of three ways:
- PASS: the case's file-state check passed and the model used a reasonably efficient tool-call \
shape.
- CONDITIONAL PASS: the case's file-state check passed, but the run was off-pattern -- either it \
took more tool calls than a competent run should need (often a sign the model stumbled into a \
rejected call and had to retry), or a case-specific soft check flagged that the model used a \
correct but token-wasteful call shape. Treat this as a yellow flag, not a clean pass and not a \
failure.
- FAIL: the case's file-state check failed, or the run raised an error.

Diagnose which klorb TOOLS (not just EditFile -- consider ReadFile pagination, ListDir, or any \
other tool that shows up in a failing or conditional case) have ergonomics that caused failures \
or retries, and propose concrete changes to those tools (their descriptions, parameter schemas, \
or behavior) that would raise the success rate. Number and prioritize your suggestions, and give \
at most the top five.
"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run klorb's tool-efficacy eval suite.")
    parser.add_argument(
        "--model", default=None,
        help=f"Model to run evals against (default: ${EVAL_MODEL_ENV_VAR}, or {DEFAULT_MODEL}).")
    parser.add_argument(
        "--self-review", action="store_true",
        help=(
            "After the run, feed the eval log back to a fresh session on the model under test "
            "and ask it to diagnose which tools caused failures/retries and propose "
            "improvements. Costs a second model round-trip; off by default."))
    return parser.parse_args(argv)


def _print_case_start(name: str, *, color: bool) -> None:
    print(colorize(f"{name}...", "cyan", enabled=color), flush=True)


def _print_case_result(result: CaseResult, *, color: bool) -> None:
    """Print `render_case_detail`'s tool-call transcript and pass/fail line for `result` —
    called as each case finishes, so a `make evals` run shows what each case actually did as it
    happens, not just the summary report printed after every case has run.
    """
    print(render_case_detail(result, color=color), flush=True)
    print(flush=True)


def _write_eval_log(
    results: list[CaseResult], *, path: Path, self_review: str | None = None,
) -> None:
    """Write `path` a non-colorized eval log: the detailed tool-call transcript for every case
    that failed or conditionally passed (see `CaseResult.conditional`), followed by the same
    `## Summary` block `render_report()` prints. A fully clean run (no failures, no conditional
    passes) has nothing worth re-reading in detail, so `path` then holds just the summary block.

    `self_review`, if given (see `--self-review`), is appended as a final `## Self-review`
    section -- the same text printed to the terminal, so the log and the console output carry
    the diagnosis identically rather than only one of them.
    """
    notable_results = [result for result in results if not result.passed or result.conditional]
    sections = [render_case_detail(result, color=False) for result in notable_results]
    sections.append(render_summary(results))
    if self_review is not None:
        sections.append(f"## Self-review\n\n{self_review}")
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


def _run_self_review(*, model: str, provider: OpenRouterApiProvider, log_path: Path) -> str:
    """Feed `log_path` (the eval log `_write_eval_log()` just wrote) to a fresh review `Session`
    on the same model/provider under test, and return its self-diagnosis text: which tools'
    ergonomics caused failures or retries, and prioritized, concrete suggestions to fix them.

    The review session's workspace is a scratch temp directory (minted via
    `create_tempdir_for_session`, read-only since the review session only needs to read the
    copied log, `remove_on_exit=True` so it's cleaned up when the process exits) holding a copy
    of `log_path` — `trusted=True` so the granted `readDirs.allow` entry actually lifts the read
    boundary onto it (see `resolve_and_evaluate_read`'s trusted-mode branch). The review session
    offers the real `klorb.tools` package so it can `ReadFile` the log itself rather than having
    the whole thing pasted into the prompt.
    """
    session_config = SessionConfig(
        model=model, interactive=False, thinking_enabled=False,
        workspace=Workspace(path=Path.cwd(), trusted=True))
    review_dir = create_tempdir_for_session(session_config, remove_on_exit=True)
    copied_log = review_dir / log_path.name
    shutil.copy(log_path, copied_log)

    tool_registry = ToolRegistry(ProcessConfig(), session_config, package=tools_package)
    session = Session(session_config, provider=provider, tool_registry=tool_registry)
    return session.send_turn(_SELF_REVIEW_PROMPT_TEMPLATE.format(log_path=copied_log))


def main(argv: list[str] | None = None) -> int:
    """Run every eval case and print a markdown report.

    Returns the process exit code: `0` if every case passed (or no `OPENROUTER_API_KEY` is
    configured, in which case evals are skipped entirely — see
    docs/adrs/tool-evals-skip-without-api-key.md), `1` if any case failed. A case that passed
    but used more tool calls than its `EvalCase.expected_tool_calls` (`CaseResult.conditional`,
    printed as `[CONDITIONAL PASS]`) still counts as passed for this exit code — it's a
    yellow-flag efficiency signal, not a correctness failure. See
    docs/adrs/eval-conditional-pass-on-excess-tool-calls.md.

    Also writes `EVAL_LOG_PATH` (`evals.log` in the current directory) via `_write_eval_log()`.
    With `--self-review`, that log is fed back to a fresh review session on the same model (see
    `_run_self_review()`) for a self-diagnosis, printed and appended to the log; the exit code
    is unaffected by the review itself, only by `results`.
    """
    load_dotenv()
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    model = args.model or os.environ.get(EVAL_MODEL_ENV_VAR) or DEFAULT_MODEL

    provider = OpenRouterApiProvider(model=model)
    try:
        provider.get_api_key()
    except RuntimeError as exc:
        print(f"Skipping evals: {exc}")
        return 0

    color = use_color()
    print()
    print(f'Running eval cases on model: {colorize(model, 'green', enabled=color)}...')
    results = run_evaluation(
        CASES, model=model, provider=provider,
        on_case_start=lambda name: _print_case_start(name, color=color),
        on_case_complete=lambda result: _print_case_result(result, color=color))
    print()
    print(render_report(results, color=color, tool_token_counts=tool_token_counts(model=model)))
    _write_eval_log(results, path=EVAL_LOG_PATH)

    self_review: str | None = None
    if args.self_review:
        print()
        print(colorize("Running self-review...", "cyan", enabled=color), flush=True)
        self_review = _run_self_review(model=model, provider=provider, log_path=EVAL_LOG_PATH)
        print()
        print(colorize("## Self-review", "cyan", enabled=color))
        print()
        print(self_review, flush=True)
        _write_eval_log(results, path=EVAL_LOG_PATH, self_review=self_review)
    else:
        print()
        print("Re-run with --self-review for an agent-authored critique of the tools.")

    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
