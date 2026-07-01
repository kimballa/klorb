# © Copyright 2026 Aaron Kimball
"""Command-line entry point for klorb."""

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from klorb.logging_config import configure_logging
from klorb.logging_config import session_log_path
from klorb.openrouter import OpenRouterApiProvider
from klorb.process_config import load_process_config
from klorb.session import Session
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
    parser.add_argument(
        "--model",
        default=None,
        help="OpenRouter model identifier to use. Defaults to the configured/process model.",
    )
    parser.add_argument(
        "--config",
        dest="config",
        default=None,
        help=(
            "Path to an additional klorb-config.json file, applied on top of the "
            "/etc, per-user, and per-project config files."
        ),
    )
    parser.add_argument(
        "--interactive",
        dest="interactive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Stay in the interactive REPL, submitting --message's prompt as the first turn "
            "if one was given. Defaults to true; defaults to false when --message is given "
            "without an explicit --interactive/--no-interactive flag."
        ),
    )
    parser.add_argument(
        "--session-log",
        dest="session_log",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Write a per-session log file. Defaults to on when interactive and off for a "
            "one-shot prompt; use --no-session-log to disable it in the REPL."
        ),
    )
    return parser


def main() -> None:
    """Parse CLI arguments and either run a single prompt or start the interactive REPL."""
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    interactive = args.prompt is None if args.interactive is None else args.interactive
    if not interactive and args.prompt is None:
        parser.error("--message is required when --no-interactive is set.")

    session_log = interactive if args.session_log is None else args.session_log

    process_config = load_process_config(
        config_flag_path=Path(args.config) if args.config is not None else None)
    process_config.session.interactive = interactive
    if args.model is not None:
        process_config.session.model = args.model

    provider = OpenRouterApiProvider(base_url=process_config.openrouter_base_url)
    session = Session(
        process_config.session.model_copy(),
        provider=provider,
        thinking_token_budgets=process_config.thinking_token_budgets,
    )

    log_path = session_log_path(session.id) if session_log else None
    configure_logging(repl_mode=interactive, log_path=log_path)
    logger.debug("Logging to %s", log_path)

    if interactive:
        run_repl(
            session,
            process_config=process_config,
            initial_message=args.prompt,
            session_log_enabled=session_log,
        )
    else:
        logger.info("Sending prompt to model=%s", session.config.model)
        streamed_any = False

        def on_chunk(delta_text: str) -> None:
            nonlocal streamed_any
            streamed_any = True
            print(delta_text, end="", flush=True)

        response = session.run_one_shot(args.prompt, on_chunk=on_chunk)
        logger.info("Received response of %d characters from model=%s", len(response), session.config.model)
        if streamed_any:
            print()
        else:
            print(response)


if __name__ == "__main__":
    main()
