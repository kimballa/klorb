# © Copyright 2026 Aaron Kimball
"""Entry point for `make evals`: runs klorb.evals.cases.CASES against a real model and prints a
markdown report. See docs/specs/tool-eval-harness.md.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from klorb.openrouter import DEFAULT_MODEL
from klorb.openrouter import OpenRouterApiProvider

from .cases import CASES
from .colors import colorize
from .colors import use_color
from .harness import CaseResult
from .harness import run_evaluation
from .harness import tool_token_counts
from .report import render_case_detail
from .report import render_report
from .report import render_summary

EVAL_MODEL_ENV_VAR = "KLORB_EVAL_MODEL"
EVAL_LOG_PATH = Path("evals.log")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run klorb's tool-efficacy eval suite.")
    parser.add_argument(
        "--model", default=None,
        help=f"Model to run evals against (default: ${EVAL_MODEL_ENV_VAR}, or {DEFAULT_MODEL}).")
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


def _write_eval_log(results: list[CaseResult], *, path: Path) -> None:
    """Write `path` a non-colorized eval log: the detailed tool-call transcript for every case
    that failed or conditionally passed (see `CaseResult.conditional`), followed by the same
    `## Summary` block `render_report()` prints. A fully clean run (no failures, no conditional
    passes) has nothing worth re-reading in detail, so `path` then holds just the summary block.
    """
    notable_results = [result for result in results if not result.passed or result.conditional]
    sections = [render_case_detail(result, color=False) for result in notable_results]
    sections.append(render_summary(results))
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


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
    results = run_evaluation(
        CASES, model=model, provider=provider,
        on_case_start=lambda name: _print_case_start(name, color=color),
        on_case_complete=lambda result: _print_case_result(result, color=color))
    print()
    print(render_report(results, color=color, tool_token_counts=tool_token_counts(model=model)))
    _write_eval_log(results, path=EVAL_LOG_PATH)
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
