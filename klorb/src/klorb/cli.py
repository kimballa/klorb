# © Copyright 2026 Aaron Kimball
"""Command-line entry point for klorb."""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from klorb import __version__
from klorb.klorb_init import InitError, InitScope, default_scope, run_init
from klorb.logging_config import configure_logging, session_log_path
from klorb.models.model import Model
from klorb.models.openrouter_pricing import (
    MAX_PRICING_REQUESTS_PER_SECOND,
    ModelPricing,
    fetch_openrouter_pricing_for_models,
)
from klorb.models.registry import ModelRegistry
from klorb.openrouter import OpenRouterApiProvider
from klorb.process_config import apply_cli_flags_to_session, load_process_config
from klorb.role import OPERATOR_ROLE_NAME, get_role
from klorb.session import Session, SessionConfig
from klorb.system_prompt import SystemPrompt
from klorb.token_estimate import configure_tiktoken_cache_env, estimate_tokens
from klorb.tools.registry import ToolRegistry
from klorb.tui import run_repl
from klorb.workspace import TrustManager

logger = logging.getLogger(__name__)

INIT_SUBCOMMAND = "init"
SYSTEM_PROMPT_SUBCOMMAND = "system-prompt"
MODELS_SUBCOMMAND = "models"
SHOW_CONFIG_SUBCOMMAND = "show-config"


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
            "  show-config       Show the merged config from all config files.\n\n"
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
    parser.add_argument(
        "--max-tool-calls-per-session",
        dest="max_tool_calls_per_session",
        type=int,
        default=None,
        help=(
            "Override the configured max tool calls allowed across this session before the "
            "turn fails. Defaults to the configured/process value."
        ),
    )
    return parser


def build_init_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb init`'s own flags (`--system`/`--user`/`--force`)
    — see `run_init_cli()`.
    """
    parser = argparse.ArgumentParser(
        prog="klorb init", description="Bootstrap a klorb-config.json and a klorb executable symlink.")
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
    `1` if `run_init` raised `InitError` partway through — see `docs/specs/klorb-init.md`.
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


def build_system_prompt_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb system-prompt`'s own flags
    (`--role`/`--model`/`--config`) — see `run_system_prompt_cli()`.
    """
    parser = argparse.ArgumentParser(
        prog="klorb system-prompt",
        description=(
            "Dump the resolved system prompt and tool definitions that klorb would send to "
            "the model, with a token-count summary at the bottom. Output goes to stdout."
        ),
    )
    parser.add_argument(
        "--role", default=OPERATOR_ROLE_NAME,
        help=(
            "Operating role to concretize the system prompt for (e.g. 'operator'). "
            "Defaults to 'operator', the same role a default session runs as."
        ),
    )
    parser.add_argument(
        "--model", default=None,
        help=(
            "OpenRouter model identifier to resolve model-specific prompt tiers for. "
            "Defaults to the model configured via the klorb-config.json file stack."
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


def _print_section(header: str, body: str) -> None:
    """Print one output section: a markdown-style header, a blank line, the body, and a
    trailing blank line, so sections are visually separated when concatenated on stdout."""
    print(f"## {header}\n")
    print(body)
    print()


def run_system_prompt_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb system-prompt`) and print the resolved
    system prompt and tool definitions to stdout, with a token-count summary at the bottom.

    Resolves the config file stack (the same `/etc`/per-user/per-project/`--config` layers
    `load_process_config()` always reads) to pick up the configured model, then layers the
    `--model` flag on top when given. The workspace is resolved via a fresh `TrustManager`
    (never bootstrapped — that needs the interactive TUI), the same non-interactive path a
    headless one-shot prompt takes: if the project isn't trusted, its per-project config layer
    is simply skipped, not prompted for.

    Output is plain text to stdout, with distinct markdown-style section headers separating
    the default system prompt (`default_sys.md`), the role-specific addendum, and the tool
    definitions JSON, followed by a summary of each section's estimated token count.
    """
    parser = build_system_prompt_parser()
    args = parser.parse_args(argv)

    load_dotenv()
    cwd = Path.cwd()
    config_flag_path = Path(args.config) if args.config is not None else None
    trust_manager = TrustManager()
    workspace = trust_manager.resolve_workspace(cwd)

    process_config = load_process_config(config_flag_path=config_flag_path, cwd=cwd, workspace=workspace)
    if args.model is not None:
        process_config.session.model = args.model

    configure_tiktoken_cache_env()

    session_config = SessionConfig(
        model=process_config.session.model,
        role_name=args.role,
        workspace=workspace,
    )
    role = get_role(args.role)
    system_prompt = SystemPrompt(session_config, role, ModelRegistry(), process_config)

    default_prompt = system_prompt.default_prompt()
    role_prompt = system_prompt.role_prompt()
    tool_registry = ToolRegistry.discover_tools(process_config, session_config)
    tool_definitions = tool_registry.tool_definitions()
    tools_json = json.dumps(tool_definitions, indent=2, default=str)

    _print_section("System Prompt (default_sys.md)", default_prompt)
    if role_prompt is not None:
        _print_section(f"Role-Specific Prompt (role: {args.role})", role_prompt)
    else:
        print(f"## Role-Specific Prompt (role: {args.role})\n")
        print("(none — no prompt file found for this role)")
        print()
    _print_section("Tool Definitions", tools_json)

    # Token-count summary.
    default_tokens = estimate_tokens(default_prompt)
    role_tokens = estimate_tokens(role_prompt) if role_prompt is not None else 0
    tools_tokens = estimate_tokens(tools_json)
    total = default_tokens + role_tokens + tools_tokens
    print("## Token Count Summary\n")
    print(f"  default_sys.md:        {default_tokens:>8,} tokens")
    if role_prompt is not None:
        print(f"  role-specific prompt:  {role_tokens:>8,} tokens")
    else:
        print(f"  role-specific prompt:  {role_tokens:>8,} tokens  (none)")
    print(f"  tool definitions:      {tools_tokens:>8,} tokens")
    print(f"  {'total':<22} {total:>8,} tokens")
    print()
    return 0


_MODELS_TABLE_HEADERS = [
    "NAME", "FAMILY", "VERSION", "CONTEXT", "MAX OUTPUT", "VISION", "THINKING", "TOOLS", "STREAM",
]
"""Column headers for `klorb models`' default table output, in the order each model's row is
built by `_model_table_row`. `--costs` appends `IN $/MTOK`/`OUT $/MTOK` after these."""

_MODELS_TABLE_GUTTER = "  "
"""Spacing between columns in `klorb models`' table output — no vertical border characters,
per `_render_models_table`."""


def build_models_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb models`'s own flags
    (`--json`/`--brief`/`--costs`) — see `run_models_cli()`.
    """
    parser = argparse.ArgumentParser(
        prog="klorb models",
        description="List every model klorb has discovered (built-in and user-added).",
    )
    parser.add_argument(
        "--json", action="store_true",
        help=(
            "Emit a JSON array of each model's data instead of a table. Combined with "
            "--brief, emits a JSON array of model name strings instead."
        ),
    )
    parser.add_argument(
        "--brief", action="store_true",
        help=(
            "Emit only each model's OpenRouter name, no other fields: one per line as plain "
            "text, or (combined with --json) as a JSON array of strings."
        ),
    )
    parser.add_argument(
        "--costs", action="store_true",
        help=(
            "Look up each model's current per-token cost from OpenRouter (live, throttled to "
            f"{MAX_PRICING_REQUESTS_PER_SECOND:g} requests/second — see "
            "klorb.models.openrouter_pricing.MAX_PRICING_REQUESTS_PER_SECOND) and include it "
            "in the output. Ignored with --brief, which never prints anything but names."
        ),
    )
    return parser


def _format_int(value: object) -> str:
    return f"{value:,}" if isinstance(value, int) else "-"


def _format_bool(value: object) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "-"


def _model_table_row(model: Model, pricing: ModelPricing | None, *, include_costs: bool) -> list[str]:
    capabilities = model.capabilities()
    row = [
        model.name(),
        model.family() or "-",
        model.model_version() or "-",
        _format_int(capabilities.get("max_context_window")),
        _format_int(capabilities.get("max_output_tokens")),
        _format_bool(capabilities.get("vision")),
        _format_bool(capabilities.get("thinking")),
        _format_bool(capabilities.get("function_calling")),
        _format_bool(capabilities.get("streaming")),
    ]
    if include_costs:
        if pricing is None:
            row += ["-", "-"]
        else:
            row += [f"{pricing.input_cost_per_mtok:.3f}", f"{pricing.output_cost_per_mtok:.3f}"]
    return row


def _render_models_table(models: list[Model], costs: dict[str, ModelPricing | None] | None) -> str:
    """Render `models` as a column-aligned table with no vertical borders between columns and a
    single horizontal rule under the header row (and nowhere else). `costs` (from `--costs`),
    if given, appends an input/output $-per-MTok column pair; a model with no live pricing
    available shows `-` in both.
    """
    headers = list(_MODELS_TABLE_HEADERS)
    if costs is not None:
        headers += ["IN $/MTOK", "OUT $/MTOK"]

    rows: list[list[str]] = []
    for model in models:
        pricing = costs.get(model.name()) if costs is not None else None
        rows.append(_model_table_row(model, pricing, include_costs=costs is not None))

    widths = [max(len(header), *(len(row[i]) for row in rows)) if rows else len(header)
              for i, header in enumerate(headers)]

    right_justified = {"CONTEXT", "MAX OUTPUT", "IN $/MTOK", "OUT $/MTOK"}

    def render_row(values: list[str]) -> str:
        parts = []
        for value, header, width in zip(values, headers, widths):
            align = "rjust" if header in right_justified else "ljust"
            parts.append(getattr(value, align)(width))
        return _MODELS_TABLE_GUTTER.join(parts).rstrip()

    total_width = sum(widths) + len(_MODELS_TABLE_GUTTER) * (len(widths) - 1)
    lines = [render_row(headers), "-" * total_width]
    lines.extend(render_row(row) for row in rows)
    return "\n".join(lines)


def _model_to_dict(model: Model, pricing: ModelPricing | None, *, include_costs: bool) -> dict[str, Any]:
    """Return `model`'s data as a plain JSON-serializable dict, in the same shape as its source
    `klorb-model` JSON file's data (minus the `schema` envelope, which describes the file, not
    the model). When `include_costs` is set (`--json --costs`), adds a `costs` key: `None` if
    no live pricing could be found for this model, otherwise its per-MTok input/output cost —
    see `run_models_cli`.
    """
    data: dict[str, Any] = {
        "name": model.name(),
        "family": model.family(),
        "model_version": model.model_version(),
        "settings": model.settings(),
        "capabilities": model.capabilities(),
        "klorb_capabilities": model.klorb_capabilities(),
    }
    if include_costs:
        data["costs"] = None if pricing is None else {
            "input_cost_per_mtok": pricing.input_cost_per_mtok,
            "output_cost_per_mtok": pricing.output_cost_per_mtok,
            "currency": pricing.currency,
        }
    return data


def run_models_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb models`) and print every model
    `ModelRegistry` discovers (built-in and user-added, see docs/specs/model-framework.md) to
    stdout, sorted by name: a column-aligned table by default, a JSON array of each model's
    data with `--json`, or just each model's OpenRouter name and no other fields with
    `--brief` — one per line as plain text, or (combined with `--json`) as a JSON array of
    name strings.

    `--costs` looks up each model's live per-token pricing from OpenRouter
    (`klorb.models.openrouter_pricing.fetch_openrouter_pricing_for_models`, throttled to
    `MAX_PRICING_REQUESTS_PER_SECOND` requests/second) and folds it into whichever output
    format was chosen — an extra column pair in the table, or a `"costs"` key in each `--json`
    object. It's a no-op with `--brief`, which never fetches pricing since it never prints
    anything but names. Always returns `0`.
    """
    parser = build_models_parser()
    args = parser.parse_args(argv)

    models = sorted(ModelRegistry().models(), key=lambda model: model.name())

    if args.brief:
        names = [model.name() for model in models]
        if args.json:
            print(json.dumps(names, indent=2))
        else:
            for name in names:
                print(name)
        return 0

    costs: dict[str, ModelPricing | None] | None = None
    if args.costs:
        costs = fetch_openrouter_pricing_for_models([model.name() for model in models])

    if args.json:
        model_dicts = []
        for model in models:
            pricing = costs.get(model.name()) if costs is not None else None
            model_dicts.append(_model_to_dict(model, pricing, include_costs=args.costs))
        print(json.dumps(model_dicts, indent=2))
        return 0

    print(_render_models_table(models, costs))
    return 0



def build_show_config_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb show-config`'s own flags (`--config`)
    -- see `run_show_config_cli()`.
    """
    parser = argparse.ArgumentParser(
        prog="klorb show-config",
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

    load_dotenv()
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
        cls=_ConfigJSONEncoder))
    return 0


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
    docs/adrs/configure-tiktoken-cache-env-after-repl-app-mounts.md.
    """
    load_dotenv()

    if len(sys.argv) > 1 and sys.argv[1] == INIT_SUBCOMMAND:
        raise SystemExit(run_init_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == SYSTEM_PROMPT_SUBCOMMAND:
        raise SystemExit(run_system_prompt_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == MODELS_SUBCOMMAND:
        raise SystemExit(run_models_cli(sys.argv[2:]))

    if len(sys.argv) > 1 and sys.argv[1] == SHOW_CONFIG_SUBCOMMAND:
        raise SystemExit(run_show_config_cli(sys.argv[2:]))

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

    # Gather CLI flag outcomes that impact the SessionConfig into a dict. We save this collection of
    # attributes because if we subsequently create new sessions, we want to be able to re-apply the session
    # config override CLI flags on those new sessions as well.
    session_cli_flags: dict[str, Any] = {"interactive": interactive}
    if args.auto_approve:
        session_cli_flags["permission_framework"] = "auto"
    elif not interactive:
        session_cli_flags["permission_framework"] = "deny"
    if args.max_tool_calls_per_turn is not None:
        session_cli_flags["max_tool_calls_per_turn"] = args.max_tool_calls_per_turn
    if args.max_tool_calls_per_session is not None:
        session_cli_flags["max_tool_calls_per_session"] = args.max_tool_calls_per_session
    process_config.argv = list(sys.argv)
    process_config.session_cli_flags = session_cli_flags
    apply_cli_flags_to_session(process_config)
    if args.log_tool_calls is True:
        process_config.log_tool_calls = True
    elif args.log_tool_calls is False:
        process_config.log_tool_calls = False

    provider = OpenRouterApiProvider(base_url=process_config.openrouter_base_url)
    session_config = process_config.session.model_copy()
    if args.model is not None:
        session_config.model = args.model
    tool_registry = ToolRegistry.discover_tools(process_config, session_config)
    session = Session(
        session_config,
        provider=provider,
        process_config=process_config,
        tool_registry=tool_registry,
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
            trust_manager=trust_manager,
            config_flag_path=config_flag_path,
        )
    else:
        configure_tiktoken_cache_env()
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
        session.close()


if __name__ == "__main__":
    main()
