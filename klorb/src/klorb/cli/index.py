# © Copyright 2026 Aaron Kimball
"""`klorb index` subcommand entry point: a thin dispatcher to `klorb.search_index.cli`'s
per-action sub-main functions."""

import sys
from collections.abc import Callable

from klorb.cli._common import INDEX_SUBCOMMAND
from klorb.search_index.cli import run_scan_cli, run_search_cli, run_stats_cli

_ACTIONS: dict[str, Callable[[list[str]], int]] = {
    "search": run_search_cli,
    "scan": run_scan_cli,
    "stats": run_stats_cli,
}


def run_index_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb index`) and dispatch to the sub-main
    function for the given action (`search`/`scan`/`stats`) in `klorb.search_index.cli`,
    passing along the rest of `argv`. Prints usage and returns 2 if no action, or an
    unrecognized one, was given.
    """
    if not argv or argv[0] not in _ACTIONS:
        actions = ", ".join(sorted(_ACTIONS))
        print(f"usage: klorb {INDEX_SUBCOMMAND} <action> [args...]\nactions: {actions}", file=sys.stderr)
        return 2
    return _ACTIONS[argv[0]](argv[1:])
