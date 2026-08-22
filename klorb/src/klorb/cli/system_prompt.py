# © Copyright 2026 Aaron Kimball
"""`klorb system-prompt` subcommand entry point."""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from klorb.agents.policy import compute_root_session_grants
from klorb.cli._common import SYSTEM_PROMPT_SUBCOMMAND, TABLE_GUTTER
from klorb.models.registry import ModelRegistry
from klorb.process_config import load_process_config
from klorb.role import OPERATOR_ROLE_NAME, get_role
from klorb.session import SessionConfig, WorkspaceAccess
from klorb.system_prompt import SystemPrompt
from klorb.token_estimate import configure_tiktoken_cache_env, estimate_tokens, tool_token_counts
from klorb.workspace import TrustManager


def build_system_prompt_parser() -> argparse.ArgumentParser:
    """Build the argument parser for `klorb system-prompt`'s own flags
    (`--role`/`--model`/`--config`).
    """
    parser = argparse.ArgumentParser(
        prog=f"klorb {SYSTEM_PROMPT_SUBCOMMAND}",
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


def _render_tool_token_table(per_tool_tokens: dict[str, int]) -> str:
    """Render `per_tool_tokens` (tool name -> its full function-calling definition's token
    count) as a column-aligned table sorted by token count descending, with a trailing total
    row.
    """
    rows = sorted(per_tool_tokens.items(), key=lambda kv: kv[1], reverse=True)
    headers = ("TOOL", "TOKENS")
    name_width = max([len(headers[0])] + [len(name) for name, _ in rows])
    tokens_width = max([len(headers[1])] + [len(f"{count:,}") for _, count in rows])

    def render_row(name: str, tokens_text: str) -> str:
        return f"{name.ljust(name_width)}{TABLE_GUTTER}{tokens_text.rjust(tokens_width)}"

    rule = "-" * (name_width + len(TABLE_GUTTER) + tokens_width)
    lines = [render_row(*headers), rule]
    lines.extend(render_row(name, f"{count:,}") for name, count in rows)
    lines.append(rule)
    lines.append(render_row("total", f"{sum(per_tool_tokens.values()):,}"))
    return "\n".join(lines)


def run_system_prompt_cli(argv: list[str]) -> int:
    """Parse `argv` (the arguments following `klorb system-prompt`) and print the resolved
    system prompt and tool definitions to stdout, with a token-count summary at the bottom.

    Resolves the config file stack to pick up the configured model, then layers the
    `--model` flag on top when given. The workspace is resolved via a fresh `TrustManager`:
    if the project isn't trusted, its per-project config layer is simply skipped, not
    prompted for.

    Output is plain text to stdout, with distinct markdown-style section headers separating
    the default system prompt (`default_sys.md`), the role-specific addendum, the tool
    definitions JSON, and a per-tool token-count table, followed by a summary of each
    section's estimated token count.
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
        workspace_access=WorkspaceAccess(workspace=workspace),
    )
    role = get_role(args.role)
    system_prompt = SystemPrompt(session_config, role, ModelRegistry(), process_config)

    default_prompt = system_prompt.default_prompt()
    role_prompt = system_prompt.role_prompt()
    grants = compute_root_session_grants(process_config, session_config, args.role)
    tool_definitions = grants.tool_registry.tool_definitions()
    tools_json_display = json.dumps(tool_definitions, indent=2, default=str, ensure_ascii=False)
    tools_json_wire = json.dumps(tool_definitions, default=str, ensure_ascii=False)

    _print_section("System Prompt (default_sys.md)", default_prompt)
    if role_prompt is not None:
        _print_section(f"Role-Specific Prompt (role: {args.role})", role_prompt)
    else:
        print(f"## Role-Specific Prompt (role: {args.role})\n")
        print("(none — no prompt file found for this role)")
        print()
    _print_section("Tool Definitions", tools_json_display)
    _print_section("Tool Token Breakdown", _render_tool_token_table(tool_token_counts(tool_definitions)))

    # Token-count summary (compact JSON matches the wire format the session sends).
    default_tokens = estimate_tokens(default_prompt)
    role_tokens = estimate_tokens(role_prompt) if role_prompt is not None else 0
    tools_tokens = estimate_tokens(tools_json_wire)
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
