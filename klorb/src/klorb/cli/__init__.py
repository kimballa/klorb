# © Copyright 2026 Aaron Kimball
"""klorb's command-line interface: the default (no-subcommand) entry point plus one module per
`klorb <subcommand>`. Each subcommand module exports a `build_<name>_parser()` and a
`run_<name>_cli(argv)`, both re-exported here so other code can drive a subcommand
programmatically without importing its module directly.
"""

from klorb.cli._common import (
    INIT_SUBCOMMAND,
    MODELS_SUBCOMMAND,
    SERVER_SUBCOMMAND,
    SHOW_CONFIG_SUBCOMMAND,
    SYSTEM_PROMPT_SUBCOMMAND,
)
from klorb.cli.init import build_init_parser, run_init_cli
from klorb.cli.main import build_parser, main
from klorb.cli.models import build_models_parser, run_models_cli
from klorb.cli.server import build_server_parser, run_server_cli
from klorb.cli.show_config import build_show_config_parser, run_show_config_cli
from klorb.cli.system_prompt import build_system_prompt_parser, run_system_prompt_cli

__all__ = [
    "INIT_SUBCOMMAND",
    "MODELS_SUBCOMMAND",
    "SERVER_SUBCOMMAND",
    "SHOW_CONFIG_SUBCOMMAND",
    "SYSTEM_PROMPT_SUBCOMMAND",
    "build_init_parser",
    "build_models_parser",
    "build_parser",
    "build_server_parser",
    "build_show_config_parser",
    "build_system_prompt_parser",
    "main",
    "run_init_cli",
    "run_models_cli",
    "run_server_cli",
    "run_show_config_cli",
    "run_system_prompt_cli",
]
