# © Copyright 2026 Aaron Kimball
"""Subcommand name constants shared between `klorb.cli.main`'s dispatch logic and each
subcommand module's own argument parser."""

INIT_SUBCOMMAND = "init"
SYSTEM_PROMPT_SUBCOMMAND = "system-prompt"
MODELS_SUBCOMMAND = "models"
SHOW_CONFIG_SUBCOMMAND = "show-config"
SERVER_SUBCOMMAND = "server"

TABLE_GUTTER = "  "
"""Column spacing for the CLI's table-formatted output (`klorb models`, `klorb system-prompt`'s
tool token-count table) — no vertical border characters between columns."""
