# © Copyright 2026 Aaron Kimball
"""`klorb show-config` subcommand entry point."""

import argparse
import json
from pathlib import Path

from klorb.cli._common import SHOW_CONFIG_SUBCOMMAND
from klorb.process_config import load_process_config
from klorb.workspace import TrustManager


def build_show_config_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb show-config`'s own flags (`--config`)
    -- see `run_show_config_cli()`.
    """
    parser = argparse.ArgumentParser(
        prog=f"klorb {SHOW_CONFIG_SUBCOMMAND}",
        description=(
            "Show the merged config from all config files (built-in defaults, /etc, "
            "per-user, per-project, and --config), pretty-printed as JSON to stdout."
        ),
    )
    parser.add_argument(
        "--config", dest="config", default=None,
        help=(
            "Path to an additional klorb-config.json file, applied on top of the "
            "/etc, per-user, and per-project config files."
        ),
    )
    return parser


def run_show_config_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb show-config`) and print the merged
    config to stdout as pretty-printed JSON.

    Resolves the config file stack (the same `/etc`/per-user/per-project/`--config` layers
    `load_process_config()` always reads). The workspace is resolved via a fresh `TrustManager`
    (never bootstrapped -- that needs the interactive TUI), the same non-interactive path a
    headless one-shot prompt takes: if the project isn't trusted, its per-project config layer
    is simply skipped, not prompted for.

    Returns 0 on success.
    """
    parser = build_show_config_parser()
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    config_flag_path = Path(args.config) if args.config is not None else None
    trust_manager = TrustManager()
    workspace = trust_manager.resolve_workspace(cwd)

    process_config = load_process_config(
        config_flag_path=config_flag_path, cwd=cwd, workspace=workspace)

    from klorb.process_config import process_config_to_disk_dict
    from klorb.schema_envelope import _ConfigJSONEncoder, _wrap_compact_list_elements

    config_dict = process_config_to_disk_dict(process_config)
    print(json.dumps(
        _wrap_compact_list_elements(config_dict), indent=2, sort_keys=True,
        cls=_ConfigJSONEncoder, ensure_ascii=False))
    return 0
