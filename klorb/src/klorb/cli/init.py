# © Copyright 2026 Aaron Kimball
"""`klorb init` subcommand entry point."""

import argparse
import sys

from klorb.cli._common import INIT_SUBCOMMAND
from klorb.klorb_init import InitError, InitScope, default_scope, run_init


def build_init_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb init`'s own flags (`--system`/`--user`/`--force`).
    """
    parser = argparse.ArgumentParser(
        prog=f"klorb {INIT_SUBCOMMAND}",
        description="Bootstrap a klorb-config.json and a klorb executable symlink.")
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--system", dest="scope", action="store_const", const="system",
        help="Install to /etc/klorb and /usr/bin. Must be run as root. Default when running as root.")
    scope_group.add_argument(
        "--user", dest="scope", action="store_const", const="user",
        help="Install to $KLORB_CONFIG_DIR and ~/.local/bin. Default when not running as root.")
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing config file or executable symlink instead of leaving it alone.")
    return parser


def run_init_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb init`) and run `klorb.klorb_init.run_init`,
    printing its progress messages to stderr as it goes. Returns the process exit status: `0`
    if every step ran (including a step that was skipped because its target already exists),
    `1` if `run_init` raised `InitError` partway through.
    """
    parser = build_init_parser()
    args = parser.parse_args(argv)
    scope: InitScope = args.scope or default_scope()

    try:
        messages = run_init(scope, force=args.force)
    except InitError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for message in messages:
        print(message, file=sys.stderr)
    return 0
