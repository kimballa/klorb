# © Copyright 2026 Aaron Kimball
"""Entry point for `make evals`: runs klorb.evals.cases.CASES against a real model and prints a
markdown report. See docs/specs/tool-eval-harness.md.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

from klorb.openrouter import DEFAULT_MODEL
from klorb.openrouter import OpenRouterApiProvider

from .cases import CASES
from .colors import colorize
from .colors import use_color
from .harness import CaseResult
from .harness import run_evaluation
from .harness import tool_token_counts
from .report import render_report
from .report import status_label

EVAL_MODEL_ENV_VAR = "KLORB_EVAL_MODEL"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run klorb's tool-efficacy eval suite.")
    parser.add_argument(
        "--model", default=None,
        help=f"Model to run evals against (default: ${EVAL_MODEL_ENV_VAR}, or {DEFAULT_MODEL}).")
    return parser.parse_args(argv)


def _print_case_start(name: str, *, color: bool) -> None:
    print(colorize(f"{name}...", "cyan", enabled=color), flush=True)


def _print_case_result(result: CaseResult, *, color: bool) -> None:
    """Print `result.tool_call_log`'s raw request/response transcript, then a pass/fail line —
    called as each case finishes, so a `make evals` run shows what each case actually did as it
    happens, not just the summary report printed after every case has run.
    """
    for entry in result.tool_call_log:
        failed = (entry.response or "").startswith("Error:")
        print(colorize(f"  -> {entry.name}({entry.arguments})", "yellow", enabled=color), flush=True)
        response_color = "red" if failed else "green"
        print(colorize(f"  <- {entry.response}", response_color, enabled=color), flush=True)
    status = status_label(result, color=color)
    print(f"  [{status}] {result.name} ({result.duration_s:.2f}s)", flush=True)


def main(argv: list[str] | None = None) -> int:
    """Run every eval case and print a markdown report.

    Returns the process exit code: `0` if every case passed (or no `OPENROUTER_API_KEY` is
    configured, in which case evals are skipped entirely — see
    docs/adrs/tool-evals-skip-without-api-key.md), `1` if any case failed. A case that passed
    but used more tool calls than its `EvalCase.expected_tool_calls` (`CaseResult.conditional`,
    printed as `[CONDITIONAL PASS]`) still counts as passed for this exit code — it's a
    yellow-flag efficiency signal, not a correctness failure. See
    docs/adrs/eval-conditional-pass-on-excess-tool-calls.md.
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
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
