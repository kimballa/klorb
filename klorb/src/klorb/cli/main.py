# © Copyright 2026 Aaron Kimball
"""klorb's default (no-subcommand) command-line entry point: run a one-shot prompt or start
the interactive REPL, and dispatch to a subcommand's own entry point when `argv[1]` names one.
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from klorb import __version__
from klorb.agents.policy import compute_root_session_grants
from klorb.cli._common import (
    INIT_SUBCOMMAND,
    MODELS_SUBCOMMAND,
    SERVER_SUBCOMMAND,
    SHOW_CONFIG_SUBCOMMAND,
    SYSTEM_PROMPT_SUBCOMMAND,
)
from klorb.cli.init import run_init_cli
from klorb.cli.models import run_models_cli
from klorb.cli.server import run_server_cli
from klorb.cli.show_config import run_show_config_cli
from klorb.cli.system_prompt import run_system_prompt_cli
from klorb.hooks.dispatcher import HookDispatcher
from klorb.hooks.hook_api import HookInput
from klorb.logging_config import configure_logging, configure_minimal_logging, session_log_path
from klorb.models.registry import ModelRegistry
from klorb.openrouter import OpenRouterApiProvider
from klorb.process_config import apply_cli_flags_to_session, load_process_config
from klorb.session import Session
from klorb.token_estimate import configure_tiktoken_cache_env
from klorb.tui import run_repl
from klorb.workspace import TrustManager

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the klorb CLI."""
    parser = argparse.ArgumentParser(
        prog="klorb",
        description="Klorb is your friendly neighborhood agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Subcommands:\n"
            "  init              Bootstrap a klorb-config.json and a `klorb` executable "
            "symlink.\n"
            "  system-prompt     Dump the resolved system prompt and tool definitions.\n"
            "  models            List every discovered model.\n"
            "  show-config       Show the merged config from all config files.\n"
            "  server            Run a persistent Agent Client Protocol (ACP) server on "
            "stdin/stdout.\n\n"
            "Run `klorb <subcommand> --help` to see subcommand-specific flags."
        ),
    )
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
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
        "--new",
        dest="new_session",
        action="store_true",
        default=False,
        help=(
            "Skip restoring the workspace's most recently touched saved session on startup; "
            "always start the REPL with a fresh session. Implied by --message/-m, so a "
            "submitted prompt always starts a fresh session rather than joining a restored "
            "one. No effect on a one-shot --message prompt without --interactive, which "
            "never restores a saved session anyway."
        ),
    )
    parser.add_argument(
        "--quit-on-success",
        dest="quit_on_success",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "In the REPL, exit the process as soon as a model turn finishes with a response and "
            "no message was queued during it. Disregarded for a turn that ends in an error, is "
            "aborted (Escape/Ctrl+C), or is followed by a queued message. Defaults to off (stay "
            "in the REPL)."
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
    parser.add_argument(
        "-y",
        "--auto-approve",
        dest="auto_approve",
        action="store_true",
        default=False,
        help=(
            "Auto-approve every tool-permission 'ask' verdict for this run (sets "
            "permissionFramework to 'auto'). Defaults to off: permissionFramework is 'ask' "
            "when interactive, 'deny' for a one-shot prompt."
        ),
    )
    parser.add_argument(
        "--log-tool-calls",
        dest="log_tool_calls",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Append every tool call's request/response to $KLORB_STATE_DIR/tool-calls.log "
            "(default ~/.local/state/klorb/tool-calls.log). Defaults to off; also enabled by the "
            "LOG_TOOL_CALLS=1/true environment variable or the tools.logCalls config key. Use "
            "--no-log-tool-calls to force it off, overriding both the config key and the "
            "LOG_TOOL_CALLS env var."
            "environment variable or the tools.logCalls config key. Use --no-log-tool-calls "
            "to force it off, overriding both the config key and the LOG_TOOL_CALLS env var."
        ),
    )
    parser.add_argument(
        "--max-tool-calls-per-turn",
        dest="max_tool_calls_per_turn",
        type=int,
        default=None,
        help=(
            "Override the configured max tool calls allowed in a single turn before the "
            "turn fails. Defaults to the configured/process value."
        ),
    )
    return parser


def _current_exit_status() -> int:
    """The exit status klorb is about to end with, inferred from whatever exception (if any) is
    currently unwinding through `main()`'s `finally` block -- 0 if none, a `SystemExit`'s own
    integer `code`, or 1 for anything else (a non-int `SystemExit.code`, or any other
    propagating exception)."""
    exc = sys.exc_info()[1]
    if exc is None:
        return 0
    if isinstance(exc, SystemExit):
        if exc.code is None:
            return 0
        if isinstance(exc.code, int):
            return exc.code
    return 1


def main() -> None:
    """Parse CLI arguments and either run a single prompt or start the interactive REPL.

    `klorb init ...` is special-cased ahead of the normal argument parsing below: it's only
    recognized when `init` is the very first argument (`sys.argv[1]`), so it can't be
    confused with an ordinary flag value or one-shot prompt appearing later in `argv` — see
    `docs/specs/klorb-init.md`.

    The current workspace's registration/trust state is resolved (never bootstrapped — that
    needs the interactive TUI, see `klorb.tui.ReplApp._resolve_workspace_trust`) via a
    fresh `TrustManager` before `load_process_config()` runs, so both a headless one-shot
    prompt and the REPL honor whatever trust decision a previous interactive session recorded
    for this directory. See docs/specs/projects-and-trust.md.

    For a one-shot prompt, calls `klorb.token_estimate.configure_tiktoken_cache_env()` once
    logging is configured (so its log message is actually visible on stderr and, if enabled,
    the session log file), pointing tiktoken at the `klorb init`-installed cache if one is
    present. For an interactive session, that same call is instead made by
    `klorb.tui.ReplApp.on_mount()` once the Textual app is running, so its log message
    routes through the app's log (or the session log file) rather than leaking to raw stderr
    ahead of the TUI taking over the terminal -- see
    docs/adrs/00107-configure-tiktoken-cache-env-after-repl-app-mounts.md.
    """
    load_dotenv()
    # Call `configure_minimal_logging()` immediately after before argument parsing or subcommand
    # dispatch -- so a log call anywhere in that window still reaches stderr.
    is_server: bool = bool(sys.argv and len(sys.argv) > 1 and sys.argv[1] == SERVER_SUBCOMMAND)
    configure_minimal_logging(is_server)

    if len(sys.argv) > 1 and sys.argv[1] == INIT_SUBCOMMAND:
        raise SystemExit(run_init_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == SYSTEM_PROMPT_SUBCOMMAND:
        raise SystemExit(run_system_prompt_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == MODELS_SUBCOMMAND:
        raise SystemExit(run_models_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == SHOW_CONFIG_SUBCOMMAND:
        raise SystemExit(run_show_config_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == SERVER_SUBCOMMAND:
        raise SystemExit(run_server_cli(sys.argv[2:]))

    parser = build_parser()
    args = parser.parse_args()

    interactive = args.prompt is None if args.interactive is None else args.interactive
    if not interactive and args.prompt is None:
        parser.error("--message is required when --no-interactive is set.")

    session_log = interactive if args.session_log is None else args.session_log

    cwd = Path.cwd()
    config_flag_path = Path(args.config) if args.config is not None else None
    trust_manager = TrustManager()
    workspace = trust_manager.resolve_workspace(cwd)

    process_config = load_process_config(config_flag_path=config_flag_path, cwd=cwd, workspace=workspace)
    provider = OpenRouterApiProvider(base_url=process_config.openrouter_base_url)
    model_registry = ModelRegistry()
    hook_dispatcher = HookDispatcher(process_config, api_provider=provider, model_registry=model_registry)
    process_start_output = hook_dispatcher.dispatch(
        "onProcessStart",
        HookInput(
            hook="onProcessStart", reason="Startup", workspace_root=str(workspace.path),
            config=process_config.model_dump(mode="json")))
    if process_start_output.log is not None:
        print(process_start_output.log)

    try:
        # Gather CLI flag outcomes that impact the SessionConfig into a dict. We save this
        # collection of attributes because if we subsequently create new sessions, we want to be
        # able to re-apply the session config override CLI flags on those new sessions as well.
        session_cli_flags: dict[str, Any] = {"interactive": interactive}
        if args.auto_approve:
            session_cli_flags["permission_framework"] = "auto"
        elif not interactive:
            session_cli_flags["permission_framework"] = "deny"
        if args.max_tool_calls_per_turn is not None:
            session_cli_flags["max_tool_calls_per_turn"] = args.max_tool_calls_per_turn
        process_config.argv = list(sys.argv)
        process_config.session_cli_flags = session_cli_flags
        apply_cli_flags_to_session(process_config)
        if args.log_tool_calls is True:
            process_config.log_tool_calls = True
        elif args.log_tool_calls is False:
            process_config.log_tool_calls = False

        session_config = process_config.session.model_copy()
        if args.model is not None:
            session_config.model = args.model
        grants = compute_root_session_grants(process_config, session_config, session_config.role_name)
        session_config.skill_rules = grants.skill_rules
        session = Session(
            session_config,
            provider=provider,
            model_registry=model_registry,
            process_config=process_config,
            tool_registry=grants.tool_registry,
            effective_subagent_roles=grants.effective_subagent_roles,
        )

        # Replace early-bird logging setup with a full config now that we have terminal / interactivity
        # flags parsed and log path established.
        log_path = session_log_path(session.id) if session_log else None
        configure_logging(repl_mode=interactive, log_path=log_path)
        logger.debug("Logging to %s", log_path)

        if interactive:
            run_repl(
                session,
                process_config=process_config,
                initial_message=args.prompt,
                session_log_enabled=session_log,
                trust_manager=trust_manager,
                config_flag_path=config_flag_path,
                skip_session_restore=args.new_session or args.prompt is not None,
                quit_on_success=args.quit_on_success,
            )
        else:
            configure_tiktoken_cache_env()
            session.register_notice_handler(print)

            def _headless_wake_handler() -> None:
                """No-op: `run_one_shot()`'s own loop (`Session.turns`) already re-checks the
                queue after every turn on this same thread, so nothing needs to be pushed here.
                Registering only tells `deliver_event_message` a host is present, so it enqueues
                the message instead of raising -- there is nothing to actively wake up."""
                logger.debug(
                    "Session %s woken by an idle event; run_one_shot's own loop will drain it.",
                    session.id)

            session.register_wake_handler(_headless_wake_handler)
            logger.info("Sending prompt to model=%s", session.config.model)
            session.fire_session_start_hook("NewSession")
            streamed_any = False

            def on_chunk(delta_text: str) -> None:
                nonlocal streamed_any
                streamed_any = True
                print(delta_text, end="", flush=True)

            response = session.run_one_shot(args.prompt, on_chunk=on_chunk)
            logger.info(
                "Received response of %d characters from model=%s", len(response), session.config.model)
            if streamed_any:
                print()
            else:
                print(response)
            session.close()
    finally:
        process_end_output = hook_dispatcher.dispatch(
            "onProcessEnd",
            HookInput(
                hook="onProcessEnd", reason="Shutdown", workspace_root=str(workspace.path),
                exit_status=_current_exit_status()))
        if process_end_output.log is not None:
            print(process_end_output.log)


if __name__ == "__main__":
    main()
