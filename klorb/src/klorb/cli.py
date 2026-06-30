# © Copyright 2026 Aaron Kimball
"""Command-line entry point for klorb."""

import argparse
import logging

from dotenv import load_dotenv

from klorb.logging_config import configure_logging
from klorb.openrouter import DEFAULT_MODEL
from klorb.openrouter import OpenRouterApiProvider
from klorb.tui.repl import run_repl

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the klorb CLI."""
    parser = argparse.ArgumentParser(prog="klorb", description="Send a prompt to a model via OpenRouter.")
    parser.add_argument(
        "-m",
        "--message",
        dest="prompt",
        default=None,
        help="The prompt to send to the model. If omitted, starts the interactive REPL.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model identifier to use.")
    parser.add_argument(
        "--session-log",
        dest="session_log",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Write a per-session log file. Defaults to on in the REPL and off for a "
            "one-shot prompt; use --no-session-log to disable it in the REPL."
        ),
    )
    return parser


def main() -> None:
    """Parse CLI arguments and either run a single prompt or start the interactive REPL."""
    load_dotenv()

    args = build_parser().parse_args()
    repl_mode = args.prompt is None
    session_log = repl_mode if args.session_log is None else args.session_log
    log_path = configure_logging(repl_mode=repl_mode, session_log=session_log)
    logger.debug("Logging to %s", log_path)

    if repl_mode:
        run_repl(model=args.model)
        return
    logger.info("Sending prompt to model=%s", args.model)
    provider = OpenRouterApiProvider()
    response = provider.send_prompt(args.prompt, model=args.model)
    logger.info("Received response of %d characters from model=%s", len(response), args.model)
    print(response)


if __name__ == "__main__":
    main()
