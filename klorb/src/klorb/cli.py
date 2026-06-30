# © Copyright 2026 Aaron Kimball
"""Command-line entry point for klorb."""

import argparse
import logging

from dotenv import load_dotenv

from klorb.logging_config import configure_logging
from klorb.openrouter import DEFAULT_MODEL
from klorb.openrouter import OpenRouterApiProvider

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the klorb CLI."""
    parser = argparse.ArgumentParser(prog="klorb", description="Send a prompt to a model via OpenRouter.")
    parser.add_argument("prompt", help="The prompt to send to the model.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenRouter model identifier to use.")
    return parser


def main() -> None:
    """Parse CLI arguments, send the prompt, and print the model's response to stdout."""
    load_dotenv()
    log_path = configure_logging()
    logger.debug("Logging to %s", log_path)

    args = build_parser().parse_args()
    logger.info("Sending prompt to model=%s", args.model)
    provider = OpenRouterApiProvider()
    response = provider.send_prompt(args.prompt, model=args.model)
    logger.info("Received response of %d characters from model=%s", len(response), args.model)
    print(response)


if __name__ == "__main__":
    main()
