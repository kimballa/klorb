# © Copyright 2026 Aaron Kimball
"""`klorb server` subcommand entry point."""

import argparse
import asyncio
import logging
from pathlib import Path

from klorb.cli._common import SERVER_SUBCOMMAND
from klorb.logging_config import configure_logging, session_log_path
from klorb.process_config import ProcessConfig, load_process_config
from klorb.server import AcpServer, ServerStreams
from klorb.session import generate_session_id
from klorb.workspace import TrustManager

logger = logging.getLogger(__name__)


def build_server_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb server`'s own flags (`--config`) -- see
    `run_server_cli()`.
    """
    parser = argparse.ArgumentParser(
        prog=f"klorb {SERVER_SUBCOMMAND}",
        description=(
            "Run a persistent klorb server process: speaks the Agent Client Protocol (ACP) -- "
            "JSON-RPC 2.0 over newline-delimited stdio -- until the client disconnects. See "
            "docs/specs/klorb-server.md."
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


async def _run_acp_server(process_config: ProcessConfig, config_flag_path: Path | None) -> int:
    """Bind an `AcpServer` to this process's real stdio and serve until the client disconnects.
    Split out from `run_server_cli()` because binding stdio (`ServerStreams.from_stdio()`)
    needs a running event loop, so the whole flow has to live inside one `asyncio.run()` call.
    `config_flag_path` is threaded through to `KlorbAcpAgent` (via `AcpServer`) so
    `_klorb/trustWorkspace` can re-resolve layered config the same way `process_config` itself
    was originally built, mirroring `klorb.tui.ReplApp`'s own `_config_flag_path`.
    """
    streams = await ServerStreams.from_stdio()
    server = AcpServer(streams, process_config, config_flag_path=config_flag_path)
    return await server.run()


def run_server_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb server`) and run a `klorb.server.AcpServer`
    -- an Agent Client Protocol (ACP) server -- against this process's own stdin/stdout until
    the client disconnects, returning 0.

    `--config` is parsed and resolved through the same `load_process_config()` file stack as
    every other subcommand (see `run_show_config_cli()`), so a malformed `--config` file is
    surfaced via `process_config.config_warnings` rather than silently ignored. The resolved
    `ProcessConfig` is the template every ACP `session/new` request's `SessionConfig` is copied
    from -- see `klorb.server.klorb_agent.KlorbAcpAgent.new_session`.

    Installs no `SIGINT` handling of its own: a `SIGINT` (e.g. Ctrl-C) is left to the
    interpreter's ordinary `KeyboardInterrupt`, which unwinds the blocked read the same way it
    would for any other Python script. It's caught here so the process exits cleanly with
    status 0 instead of printing a traceback.

    Configures logging (`configure_logging(repl_mode=False, log_path=..., stderr_json=True)`)
    before anything else runs, so every subsequent log call -- including
    `process_config.config_warnings` below -- is captured. `repl_mode=False` sends records to
    stderr (visible to a client, e.g. the VSCode plugin, that captures the server subprocess's
    stderr) in addition to the session log file; `stderr_json=True` formats each stderr line as
    one JSON object (`JsonLogFormatter`) so a client parses each record instead of treating it
    as opaque text. Unlike a one-shot prompt (see
    docs/adrs/00007-one-shot-prompts-log-to-stderr-without-a-session-file-by-default.md), a server
    process has no interactive/headless distinction to key that default off of and no single
    `Session` whose id could name the log file -- `KlorbAcpAgent.new_session` may replace it
    many times over the process's life -- so the log file is instead keyed off a
    `generate_session_id()`-shaped id minted for the server process itself.
    """
    parser = build_server_parser()
    args = parser.parse_args(argv)

    log_path = session_log_path(f"server-{generate_session_id()}")
    configure_logging(repl_mode=False, log_path=log_path, stderr_json=True)
    logger.debug("klorb server logging to %s", log_path)

    cwd = Path.cwd()
    config_flag_path = Path(args.config) if args.config is not None else None
    workspace = TrustManager().resolve_workspace(cwd)
    process_config = load_process_config(config_flag_path=config_flag_path, cwd=cwd, workspace=workspace)
    for warning in process_config.config_warnings:
        logger.warning(warning)

    try:
        return asyncio.run(_run_acp_server(process_config, config_flag_path))
    except KeyboardInterrupt:
        logger.debug("klorb server interrupted by SIGINT")
        return 0
