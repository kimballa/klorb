# © Copyright 2026 Aaron Kimball
"""Process-wide configuration: settings that live for the lifetime of the klorb process and
are shared by every `Session` created within it (today, one at a time; eventually several
running concurrently). See docs/specs/process-and-session-config.md.
"""

import importlib.resources
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from klorb.config_macros import MacroExpansionError, expand_macros, resolve_macro_values
from klorb.hooks.config import EVENT_CONFIG_MODELS, HOOK_NAMES, EventConfig, HookConfig, TimerEventConfig
from klorb.hooks.merge import concatenate_named_handler_lists, parse_handler_list
from klorb.hooks.timer_events import clamp_timer_intervals
from klorb.json_error_display import format_json_error_context
from klorb.openrouter import OPENROUTER_BASE_URL
from klorb.paths import get_klorb_config_dir
from klorb.permissions.command_access import CommandRules
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, DirRules, find_workspace_root
from klorb.permissions.domain_access import DomainRules
from klorb.permissions.file_access import FileRules
from klorb.permissions.skill_access import SkillId, SkillRules, parse_fqsn
from klorb.schema_envelope import parse_versioned_json, read_versioned_json, write_versioned_json
from klorb.session import THINKING_EFFORT_TOKEN_BUDGETS, SessionConfig, ThinkingEffort
from klorb.tool_call_log import LOG_TOOL_CALLS_CONFIG_KEY
from klorb.workspace import Workspace

logger = logging.getLogger(__name__)

DEFAULT_READ_FILE_MAX_LINES = 200
"""`ReadFileTool`'s per-call page size default; the canonical source of this value —
`klorb.tools.read_file` has no constant of its own, it reads `ProcessConfig.read_file_max_lines`
via `ToolSetupContext` at construction time instead."""

DEFAULT_GREP_MAX_RESULTS = 100
"""`GrepTool`'s per-call match cap default; the canonical source of this value —
`klorb.tools.grep` has no constant of its own, it reads `ProcessConfig.grep_max_results` via
`ToolSetupContext` at construction time instead."""

DEFAULT_GREP_CONTEXT_LINES = 2
"""`GrepTool`'s per-match context-line count default (lines of surrounding content shown on
each side of a match); the canonical source of this value — `klorb.tools.grep` has no constant
of its own, it reads `ProcessConfig.grep_context_lines` via `ToolSetupContext` at construction
time instead."""

DEFAULT_GREP_MAX_LINE_LENGTH = 500
"""`GrepTool`'s per-line character cap default — a reported line longer than this is truncated
(with a `"[truncated...]"` suffix) so a single pathologically-formatted line (a minified
sourcemap, a one-line JSON blob) can't dump an outsized chunk of text into the model's context;
the canonical source of this value — `klorb.tools.grep` has no constant of its own, it reads
`ProcessConfig.grep_max_line_length` via `ToolSetupContext` at construction time instead."""

DEFAULT_READ_FILE_MAX_LINE_LENGTH = 500
"""`ReadFileCore`'s per-line character cap default — a line longer than this is wrapped (not
truncated) at `max_line_length` characters, with each wrapped segment counting toward the
`read_file_max_lines` page-size limit. The canonical source of this value — `ReadFileCore` has
no constant of its own, it reads `ProcessConfig.read_file_max_line_length` via `ToolSetupContext`
at construction time instead."""

DEFAULT_GREP_SPILL_BYTES = 32 * 1024
"""Byte threshold on the JSON-serialized size of `GrepTool`'s `files` result value above which
it's spilled to a file (reporting `results_data_file` instead of `files` in the result) rather
than returned inline — mirrors `DEFAULT_BASH_SPILL_BYTES`'s rationale for `BashTool`'s
`stdout`/`stderr`. The canonical source of this value — `klorb.tools.grep` has no constant of
its own, it reads `ProcessConfig.grep_spill_bytes` via `ToolSetupContext` at construction time
instead."""

DEFAULT_FIND_FILE_MAX_RESULTS = 100
"""`FindFileTool`'s per-call match cap default; the canonical source of this value —
`klorb.tools.find_file` has no constant of its own, it reads
`ProcessConfig.find_file_max_results` via `ToolSetupContext` at construction time instead."""

DEFAULT_SCRATCHPAD_CONTEXT_LINES = 2
"""`SearchScratchpadTool`'s per-match context-line count default (lines of surrounding content
shown on each side of a match); the canonical source of this value — `klorb.tools.
scratchpad.search` has no constant of its own, it reads `ProcessConfig.scratchpad_context_lines`
via `ToolSetupContext` at construction time instead."""

DEFAULT_MENTION_MAX_LINES = 500
"""`@mention` file-inlining's per-file line cap default; the canonical source of this value --
`klorb.session.mixins.mentions` reads `ProcessConfig.mention_max_lines` at runtime instead."""

CONFIG_SCHEMA_NAME = "klorb-config"
CONFIG_SCHEMA_VERSION = "1.0.0"
CONFIG_FILENAME = "klorb-config.json"

DEFAULT_CONFIG_RESOURCE_NAME = "default-config.json"
"""Filename of the packaged, built-in-defaults config layer within the `klorb.resources`
package — see `_default_config_layer()`. Distinct from `klorb.klorb_init`'s
`TEMPLATE_CONFIG_RESOURCE_NAME` (`template-config.json`), the spartan starter file `klorb
init` copies to disk; this one is never copied anywhere; it's read directly, every process
start, as the lowest-precedence layer below."""

KLORB_ETC_CONFIG_ENV_VAR = "KLORB_ETC_CONFIG"
DEFAULT_ETC_CONFIG_PATH = Path("/etc/klorb") / CONFIG_FILENAME

SESSION_DEFAULTS_KEY = "sessionDefaults"

THEME_CONFIG_KEY = "ui.theme"
"""On-disk `klorb-config.json` key for `ProcessConfig.theme` — the sole canonical spelling,
shared by `PROCESS_KEY_MAP` (reading it back) and `persist_theme` (writing it), so the two
never drift apart."""

SIDEBAR_CONFIG_KEY = "ui.sidebar"
"""On-disk `klorb-config.json` key for `ProcessConfig.sidebar` — the sole canonical spelling,
shared by `PROCESS_KEY_MAP` (reading it back) and `persist_sidebar` (writing it),
so the two never drift apart."""

DEFAULT_PROMPT_INPUT_MAX_LINES = 12
"""Default max soft-wrapped-line height for the REPL's prompt textarea before it scrolls
instead of growing further; see `ProcessConfig.prompt_input_max_lines`."""

DEFAULT_SHELL_COMMAND = "/bin/bash"
"""Default shell binary a `!`-prefixed REPL command is run through; see
`ProcessConfig.shell_command`. The sole canonical source of this value — `klorb.tui.shell`'s
`UserShellCommand` takes `shell_path` as a required constructor argument rather than
defaulting it itself, so this string isn't duplicated across the two modules."""

DEFAULT_BASH_COMMAND = "/bin/bash"
"""Default shell binary `BashTool` runs a model-requested command through; see
`ProcessConfig.bash_command` and `klorb.tools.bash`."""

DEFAULT_BASH_TIMEOUT_SECONDS = 120.0
"""Default wall-clock seconds `BashTool` lets one command run before killing it; see
`ProcessConfig.bash_timeout_seconds`."""

DEFAULT_BASH_SPILL_BYTES = 8192
"""Default per-stream size threshold above which `BashTool` spills `stdout`/`stderr` to a file
(reporting `stdout_file`/`stderr_file` instead of inline text) rather than returning it directly
in the tool result — see `ProcessConfig.bash_spill_bytes` and
docs/specs/bash-tool-and-command-permissions.md."""

DEFAULT_WATCHDOG_TIMEOUT_SECONDS = 10.0
"""Default for `ProcessConfig.watchdog_timeout_seconds` — how many seconds the main-thread event
loop may go without snoozing the liveness watchdog before it force-exits the process. See
`klorb.watchdog.LivenessWatchdog` and docs/specs/interrupt-and-liveness-watchdog.md. A value of
`0` or less disables the watchdog entirely."""

DEFAULT_SHFMT_COMMAND = "shfmt"
"""Default `shfmt` binary name `BashTool` parses commands through (via
`klorb.permissions.shell_parse.parse_command`) — resolved off `PATH` by default, since the
`shfmt-py` pypi package installs it there; see `ProcessConfig.shfmt_command`."""

DEFAULT_BASH_RISK_CLASSIFIER_ENABLED = True
"""Default for `ProcessConfig.bash_risk_classifier_enabled`; see
`klorb.permissions.risk_classifier` and docs/specs/bash-tool-and-command-permissions.md's "LLM
risk classifier" section."""

DEFAULT_BASH_RISK_CLASSIFIER_MODEL = "openai/gpt-5-nano"
"""Last-resort model `klorb.permissions.risk_classifier.resolve_item_risk_assessment` falls
back to when `ProcessConfig.bash_risk_classifier_model` is unset (the normal case) and no
registered model declares the `"BASH_SAFETY_EVAL"` `klorb_capabilities` flag — independent of
`SessionConfig.model` (the main conversation's own model), since an ask can happen regardless of
which model is driving the conversation."""

DEFAULT_BASH_RISK_CLASSIFIER_TIMEOUT_SECONDS = 5.0
"""Default for `ProcessConfig.bash_risk_classifier_timeout_seconds` — a short, separate timeout
from `bash_timeout_seconds` (which bounds the actual shell command's own runtime): this bounds an
interactive round trip that happens before the command even runs, so it should fail fast rather
than stall the approval panel."""

DEFAULT_BASH_RISK_CLASSIFIER_E2E_TIMEOUT_SECONDS = 10.0
"""Default for `ProcessConfig.bash_risk_classifier_e2e_timeout_seconds` — a hard wall-clock ceiling
on the *entire* `klorb.permissions.risk_classifier.classify_command_risk()` call (its initial
request plus the one parse-retry it may make), enforced by cancelling the in-flight stream when it
elapses. Distinct from `bash_risk_classifier_timeout_seconds`, which the underlying client applies
per *request* — and, for a streaming reply, effectively per socket read: a reply that keeps
trickling bytes can run far past that per-request budget without any single read ever tripping it.
This end-to-end cap is what actually bounds how long the approval panel can wait on the classifier,
and must stay below `watchdog_timeout_seconds` so a slow (but not wedged) classifier can't be
mistaken for a hung event loop and force-exit the process."""

DEFAULT_BASH_RISK_CLASSIFIER_TOO_RISKY_THRESHOLD = 9
"""Default for `ProcessConfig.bash_risk_classifier_too_risky_threshold` — the `risk_score`
(inclusive) at or above which `klorb.tui.ReplApp._confirm_permission_ask` pre-selects
`PermissionAskPanel`'s `Deny, once` cell instead of the remembered previous cell; see
`klorb.permissions.risk_classifier.CommandRiskReport`."""

DEFAULT_BASH_RISK_CLASSIFIER_HISTORY_SIZE = 20
"""Default for `ProcessConfig.bash_risk_classifier_history_size` — how many of the most recent
`klorb.permissions.risk_classifier.HistoryEntry` records `resolve_item_risk_assessment` sends
along with the item actually being scored, so a run of similar approvals/denials earlier in the
same session can inform how aggressively the classifier generalizes its next `suggested_pattern`
— see `klorb.permissions.risk_classifier.record_decision_history`."""

DEFAULT_BASH_NETWORK_ENABLED = True
"""Default for `ProcessConfig.bash_network_enabled` — whether a sandboxed `Bash` command gets a
domain-gated network-egress proxy at all (`klorb.sandbox.network`); `False` is a full escape
hatch, mirroring `bash_risk_classifier_enabled`: the sandbox reverts to today's unconditional
`--unshare-net`-denies-everything behavior, no proxy/relay stood up. See
docs/specs/bash-tool-and-command-permissions.md's "Network egress (domain-gated proxy)"
section."""

DEFAULT_BASH_NETWORK_RECOGNIZED_CLIENTS = [
    "curl", "wget", "git", "pip", "pip3", "uv", "npm", "yarn", "pnpm",
    "go", "cargo", "mvn", "nc", "ncat", "telnet", "http", "https",
]
"""Default for `ProcessConfig.bash_network_recognized_clients` — the argv0 allowlist
`BashTool._classify`'s network-command scanner treats as "this command makes outbound network
calls, so scan its literal arguments for a target domain." See
docs/specs/bash-tool-and-command-permissions.md's "Pre-flight scanning" section for why
`ssh`/`scp`/`rsync` are deliberately excluded."""

DEFAULT_BASH_NETWORK_SOCKS_PORT = 10800
"""Default for `ProcessConfig.bash_network_socks_port` — the fixed loopback port
`klorb.sandbox.network`'s in-sandbox relay stub listens on for SOCKS5 (`ALL_PROXY`) traffic.
Adjustable in case something else on the host is already listening on this port."""

DEFAULT_BASH_NETWORK_HTTP_CONNECT_PORT = 10801
"""Default for `ProcessConfig.bash_network_http_connect_port` — the fixed loopback port
`klorb.sandbox.network`'s in-sandbox relay stub listens on for HTTP CONNECT (`HTTP_PROXY`/
`HTTPS_PROXY`) traffic. Adjustable in case something else on the host is already listening on
this port."""

DEFAULT_WEB_FETCH_TIMEOUT_SECONDS = 120.0
"""Default wall-clock seconds `WebFetch` lets an HTTP request run before timing out — see
`ProcessConfig.web_fetch_timeout_seconds` and `tools.webFetch.timeout`."""

DEFAULT_WEB_FETCH_CONNECT_TIMEOUT_SECONDS = 5.0
"""Default seconds `WebFetch` waits for a TCP connection before giving up — capped at
`web_fetch_timeout_seconds` to fail fast on unreachable hosts. See
`ProcessConfig.web_fetch_connect_timeout_seconds` and `tools.webFetch.connectTimeout`."""

DEFAULT_WEB_FETCH_SPILL_BYTES = 32768
"""Default byte threshold above which `WebFetch` spills the response body to a file instead of
returning it inline — see `ProcessConfig.web_fetch_spill_bytes` and
`tools.webFetch.spillBytes`."""

DEFAULT_WEB_FETCH_MAX_BODY_BYTES = 10 * 1024 * 1024
"""Default maximum response body bytes `WebFetch` will read — see
`ProcessConfig.web_fetch_max_body_bytes` and `tools.webFetch.maxBodyBytes`."""

ABSOLUTE_MAX_BODY_BYTES = 256 * 1024 * 1024
"""Hard ceiling on response body bytes, regardless of config — `WebFetch` aborts reading if
the body exceeds this, to avoid consuming unbounded memory."""

DEFAULT_IMAGE_DEFAULT_MAX_DIMENSION_PX = 1568
"""Long-edge downscale cap `klorb.images.prepare.prepare_image_for_model` falls back to for a
model with no `vision_details.max_width_px`/`max_height_px`/`max_megapixels` — Anthropic's own
general-purpose recommendation. See `ProcessConfig.image_default_max_dimension_px` and
`tools.images.defaultMaxDimensionPx`."""

DEFAULT_IMAGE_MAX_BYTES_RAW = 25 * 1024 * 1024
"""Default post-encode byte ceiling `klorb.images.prepare.prepare_image_for_model` enforces on
a prepared image before giving up (`klorb.images.prepare.ImageTooLargeError`). See
`ProcessConfig.image_max_bytes_raw` and `tools.images.maxBytesRaw`."""

DEFAULT_IMAGE_PREFERRED_FORMATS: list[str] = ["image/webp", "image/png"]
"""Default transcode preference order `klorb.images.prepare.prepare_image_for_model` tries, in
order, filtered against the active model's `vision_details.supported_mime_types`. See
`ProcessConfig.image_preferred_formats` and `tools.images.preferredFormats`."""

DEFAULT_SUBAGENTS_MAX_DEPTH = 2
"""Default for `ProcessConfig.subagents_max_depth`: how many levels below the top-level
(user-facing) session a subagent tree may nest -- depth 1 is the user's agent's own
subagents, depth 2 their subagents' subagents; `CreateSubagent` rejects a call that would
exceed it. See docs/specs/subagents.md."""

DEFAULT_SUBAGENTS_MAX_CONCURRENT_PER_PARENT = 4
"""Default for `ProcessConfig.subagents_max_concurrent_per_parent`: the most subagents any
single agent may have running or finished-but-undelivered at once; `CreateSubagent` rejects a
call that would exceed it. See docs/specs/subagents.md."""

DEFAULT_SUBAGENTS_MAX_ACTIVE_TOTAL = 16
"""Default for `ProcessConfig.subagents_max_active_total`: the most subagents that may be
simultaneously active (running or finished-but-undelivered) across an entire session tree,
regardless of which agent created them; `CreateSubagent` rejects a call that would exceed it.
See docs/specs/subagents.md."""

DEFAULT_SESSION_CLASSIFIER_MODEL = "openai/gpt-5-nano"
"""Last-resort model `klorb.session_naming._default_naming_model` falls back to when
`ProcessConfig.session_classifier_model` is unset (the normal case) and no registered model
declares the `"NANO_CLASSIFIER"` `klorb_capabilities` flag — independent of `SessionConfig.model`
(the main conversation's own model). Named generically (not e.g. `SESSION_NAMER_MODEL`) since
this same small/cheap classifier model choice may be reused for other structured-output tasks
beyond session naming."""

DEFAULT_SESSION_CLASSIFIER_TIMEOUT_SECONDS = 5.0
"""Default for `ProcessConfig.session_classifier_timeout_seconds` — mirrors
`DEFAULT_BASH_RISK_CLASSIFIER_TIMEOUT_SECONDS`'s rationale: a short, fail-fast per-request
budget for a classifier call that happens before the session's real turn is dispatched."""

DEFAULT_SESSION_CLASSIFIER_E2E_TIMEOUT_SECONDS = 10.0
"""Default for `ProcessConfig.session_classifier_e2e_timeout_seconds` — a hard wall-clock ceiling
on the entire `klorb.session_naming.generate_session_name()` call (initial request plus one
parse-retry), mirroring `DEFAULT_BASH_RISK_CLASSIFIER_E2E_TIMEOUT_SECONDS`'s rationale."""

SESSION_KEY_MAP: dict[str, str] = {
    "model": "model",
    "thinking.enabled": "thinking_enabled",
    "thinking.effort": "thinking_effort",
    "tools.maxCallsPerTurn": "max_tool_calls_per_turn",
    "tools.hooks.maxChainedTurns": "max_chained_hook_turns",
    "tools.memory.writePermission": "memory_write_permission",
    "tools.memory.deletePermission": "memory_delete_permission",
    "search.workspaceIndex.enabled": "search_workspace_index_enabled",
    "search.indexer.gpuEnabled": "search_indexer_gpu_enabled",
}
"""Maps each recognized key inside a `klorb-config.json` file's `sessionDefaults` object to
the `SessionConfig` attribute it sets. `interactive` is deliberately absent: it's always
inferred from CLI flags (`-m`/`--interactive`/`--no-interactive`), never config-file-driven.
`permission_framework` is likewise deliberately absent, for the same reason: its effective
default depends on the CLI-resolved `interactive` value (`"ask"` interactively, `"deny"`
headlessly), resolved by `klorb.cli.main()` — see docs/specs/permissions.md and
[[default-permission-framework-to-deny-headlessly]]. `role_name` is likewise deliberately
absent: the operating role is set by code (defaulting to the operator role), never by a
config file — see docs/specs/roles-and-system-prompts.md.
`readDirs`/`writeDirs`/`readFiles`/`writeFiles`/`commandRules`/`skillRules`/`webDomains`/
`bashDomains`/`shareEnv`/`setEnv` are also deliberately absent — lists and maps like `readDirs`
and `shareEnv` are merged (by list concatenation, or key-by-key for `setEnv`, where a later
layer's value for the same key replaces an earlier layer's) rather than the 1:1 scalar
replacement `_route_keys()` implements, so `load_process_config()` handles all nine separately,
ahead of `_route_keys()` — see docs/specs/permissions.md and docs/specs/skills.md and
docs/specs/bash-tool-and-command-permissions.md. `workspace` is deliberately absent too:
it has no on-disk key at all, by design — see `SessionConfig.workspace`/
docs/specs/projects-and-trust.md — a project must never be able to grant itself trust via its
own config file.
"""

PROCESS_KEY_MAP: dict[str, str] = {
    "thinking.tokenBudgets": "thinking_token_budgets",
    "terminal.input.maxLines": "prompt_input_max_lines",
    "tools.readFile.maxLines": "read_file_max_lines",
    "tools.readFile.maxLineLength": "read_file_max_line_length",
    "tools.@mention.maxLines": "mention_max_lines",
    "tools.grep.maxResults": "grep_max_results",
    "tools.grep.contextLines": "grep_context_lines",
    "tools.grep.maxLineLength": "grep_max_line_length",
    "tools.grep.spillBytes": "grep_spill_bytes",
    "tools.findFile.maxResults": "find_file_max_results",
    "tools.scratchpad.contextLines": "scratchpad_context_lines",
    "providers.openrouter.baseUrl": "openrouter_base_url",
    "shell.command": "shell_command",
    "shell.timeout": "shell_timeout_seconds",
    "watchdog.timeout": "watchdog_timeout_seconds",
    "tools.bash.command": "bash_command",
    "tools.bash.timeout": "bash_timeout_seconds",
    "tools.bash.spillBytes": "bash_spill_bytes",
    "hooks.bash.timeoutSeconds": "hook_bash_timeout_seconds",
    "tools.bash.shfmtCommand": "shfmt_command",
    "tools.bash.riskClassifier.enabled": "bash_risk_classifier_enabled",
    "tools.bash.riskClassifier.model": "bash_risk_classifier_model",
    "tools.bash.riskClassifier.timeout": "bash_risk_classifier_timeout_seconds",
    "tools.bash.riskClassifier.e2eTimeout": "bash_risk_classifier_e2e_timeout_seconds",
    "tools.bash.riskClassifier.tooRiskyThreshold": "bash_risk_classifier_too_risky_threshold",
    "tools.bash.riskClassifier.historySize": "bash_risk_classifier_history_size",
    "tools.bash.network.enabled": "bash_network_enabled",
    "tools.bash.network.recognizedClients": "bash_network_recognized_clients",
    "tools.bash.network.socksPort": "bash_network_socks_port",
    "tools.bash.network.httpConnectPort": "bash_network_http_connect_port",
    "tools.webFetch.timeout": "web_fetch_timeout_seconds",
    "tools.webFetch.connectTimeout": "web_fetch_connect_timeout_seconds",
    "tools.webFetch.spillBytes": "web_fetch_spill_bytes",
    "tools.webFetch.maxBodyBytes": "web_fetch_max_body_bytes",
    "tools.images.defaultMaxDimensionPx": "image_default_max_dimension_px",
    "tools.images.maxBytesRaw": "image_max_bytes_raw",
    "tools.images.preferredFormats": "image_preferred_formats",
    "tools.subagents.maxDepth": "subagents_max_depth",
    "tools.subagents.maxConcurrentPerParent": "subagents_max_concurrent_per_parent",
    "tools.subagents.maxActiveTotal": "subagents_max_active_total",
    "classifier.model": "session_classifier_model",
    "classifier.timeout": "session_classifier_timeout_seconds",
    "classifier.e2eTimeout": "session_classifier_e2e_timeout_seconds",
    "compatibility.claudeMarkdown": "compatibility_claude_markdown",
    "compatibility.claudeSkills": "compatibility_claude_skills",
    LOG_TOOL_CALLS_CONFIG_KEY: "log_tool_calls",
    THEME_CONFIG_KEY: "theme",
    SIDEBAR_CONFIG_KEY: "sidebar",
    "hooks": "hooks",
    "events": "events",
}
"""Maps each recognized top-level `klorb-config.json` key (outside `sessionDefaults`) to the
process-only `ProcessConfig` attribute it sets."""

# On-disk keys are flat strings with dot-delineated namespaces in lowerCamelCase
# (`"thinking.tokenBudgets"`, matching VSCode/Claude Code settings-file style — see
# docs/specs/process-and-session-config.md), independent of the snake_case Python attribute
# names on the pydantic models that actually hold the values.


class ProcessConfig(BaseModel):
    """Configuration shared by every `Session` created within this process's lifetime.

    `session` holds the template a fresh `SessionConfig` is copied from whenever a new
    `Session` is created (at startup, or via `/clear`) — see `SessionConfig` for what lives
    there. The remaining fields are process-only: settings that must stay identical across
    every concurrently running session (a UI limit that shouldn't differ between two
    sessions on screen at once, a shared policy table, an endpoint to talk to) rather than
    something a session can individually override.
    """

    session: SessionConfig = SessionConfig()
    prompt_input_max_lines: int = DEFAULT_PROMPT_INPUT_MAX_LINES
    thinking_token_budgets: dict[ThinkingEffort, int] = dict(THINKING_EFFORT_TOKEN_BUDGETS)
    read_file_max_lines: int = DEFAULT_READ_FILE_MAX_LINES
    read_file_max_line_length: int = DEFAULT_READ_FILE_MAX_LINE_LENGTH
    """Maximum character length of a single line before `ReadFileCore` wraps it — see
    `klorb.tools.util.read_file_core`."""
    mention_max_lines: int = DEFAULT_MENTION_MAX_LINES
    """Per-file line cap for `@mention` file inlining in user prompts -- see
    `klorb.session.mixins.mentions` and docs/specs/at-mention-file-inlining.md."""
    grep_max_results: int = DEFAULT_GREP_MAX_RESULTS
    grep_context_lines: int = DEFAULT_GREP_CONTEXT_LINES
    """Number of lines of surrounding context `GrepTool` shows on each side of a matching
    line — see `klorb.tools.grep`."""
    grep_max_line_length: int = DEFAULT_GREP_MAX_LINE_LENGTH
    """Maximum character length of a single reported line before `GrepTool` truncates it (with
    a `"[truncated...]"` suffix) — see `klorb.tools.grep`."""
    grep_spill_bytes: int = DEFAULT_GREP_SPILL_BYTES
    """JSON-serialized byte threshold above which `GrepTool` spills its `files` result value to
    a file (reporting `results_data_file` instead) rather than returning it inline — see
    `klorb.tools.grep`."""
    find_file_max_results: int = DEFAULT_FIND_FILE_MAX_RESULTS
    scratchpad_context_lines: int = DEFAULT_SCRATCHPAD_CONTEXT_LINES
    """Number of lines of surrounding context `SearchScratchpadTool` shows on each side of a
    match — see `klorb.tools.scratchpad.search`."""
    openrouter_base_url: str = OPENROUTER_BASE_URL
    shell_command: str = DEFAULT_SHELL_COMMAND
    """Shell binary a `!`-prefixed REPL command is run through, e.g. `/bin/bash` or `/bin/zsh`
    — see `klorb.tui.shell.UserShellCommand`."""
    shell_timeout_seconds: float | None = None
    """Maximum wall-clock seconds a `!`-prefixed REPL command may run before it's killed.
    `None` (the default) means no timeout is enforced."""
    bash_command: str = DEFAULT_BASH_COMMAND
    """Shell binary `BashTool` runs a model-requested command through, e.g. `/bin/bash` — see
    `klorb.tools.bash`. Distinct from `shell_command`: that one is for the user's own
    `!`-prefixed REPL commands (trusted, unchecked), this one is for model-requested commands
    (parsed and permission-checked before running)."""
    bash_timeout_seconds: float = DEFAULT_BASH_TIMEOUT_SECONDS
    """Maximum wall-clock seconds one `BashTool` command may run before it's killed and reported
    back to the model as timed out. Unlike `shell_timeout_seconds`, always enforced (not
    `None`-able)."""
    bash_spill_bytes: int = DEFAULT_BASH_SPILL_BYTES
    """Per-stream `stdout`/`stderr` byte threshold above which `BashTool` reports a file path
    (`stdout_file`/`stderr_file`) instead of the content itself, so a chatty command can't
    overrun the model's context — see `klorb.tools.bash`."""
    hook_bash_timeout_seconds: float | None = None
    """Maximum wall-clock seconds a `type="bash"` hook handler's subprocess may run before it's
    killed and treated as contributing nothing to its hook's outcome — see
    `klorb.hooks.bash_handler.run_bash_handler`. `None` (the default) falls back to
    `bash_timeout_seconds`."""
    shfmt_command: str = DEFAULT_SHFMT_COMMAND
    """`shfmt` binary `BashTool` parses a requested command through before evaluating it against
    `SessionConfig.command_rules` — see `klorb.permissions.shell_parse`."""
    watchdog_timeout_seconds: float = DEFAULT_WATCHDOG_TIMEOUT_SECONDS
    """Seconds the main-thread event loop may go without snoozing the liveness watchdog before it
    force-exits the process (best-effort session save + all-thread stack dump first) — the
    last-ditch escape from a wedged event loop. `0` or less disables the watchdog. See
    `klorb.watchdog.LivenessWatchdog`, `klorb.tui.ReplApp._snooze_watchdog`, and
    docs/specs/interrupt-and-liveness-watchdog.md."""
    bash_risk_classifier_enabled: bool = DEFAULT_BASH_RISK_CLASSIFIER_ENABLED
    """Whether `klorb.tui.ReplApp._confirm_permission_ask` classifies a `BashTool` ask's
    risk via `klorb.permissions.risk_classifier.classify_command_risk()` before showing
    `PermissionAskPanel` — an escape hatch for a user who doesn't want command text sent to a
    second LLM call at all (cost, latency, or data-sensitivity reasons). `False` means exactly
    today's behavior: no risk badge/rationale, `klorb.permissions.command_grant.
    compute_command_grant_patterns()`'s literal-argv fallback used as-is."""
    bash_risk_classifier_model: str | None = None
    """Model `classify_command_risk()` sends its request to, via the same `ApiProvider` instance
    the main conversation uses. One fixed model classifies every request regardless of how
    concerning the deterministic layer's own findings are — see `klorb.permissions.
    risk_classifier._build_system_prompt` for how conservatism for a `ForcedAskReason`-carrying
    item is instead achieved by varying the prompt, not by escalating to a costlier model.

    `None` (the default, and `default-config.json`'s own packaged value) means klorb picks the
    model itself: `klorb.permissions.risk_classifier.resolve_item_risk_assessment` looks up a
    registered model whose `Model.klorb_capabilities()` reports `"BASH_SAFETY_EVAL"`
    (`klorb.models.registry.ModelRegistry.find_by_capability`), falling back to
    `DEFAULT_BASH_RISK_CLASSIFIER_MODEL` if none is registered. Setting this to an explicit
    model name (a user's own `klorb-config.json`, or a project's) always wins over that
    lookup — klorb only picks for itself when nobody's told it what to use."""
    bash_risk_classifier_timeout_seconds: float = DEFAULT_BASH_RISK_CLASSIFIER_TIMEOUT_SECONDS
    """Wall-clock seconds `classify_command_risk()`'s request is allowed to take before it's
    treated as a failure (falling back to no risk badge/rationale) — separate from
    `bash_timeout_seconds`, which bounds the shell command's own runtime instead."""
    bash_risk_classifier_e2e_timeout_seconds: float = DEFAULT_BASH_RISK_CLASSIFIER_E2E_TIMEOUT_SECONDS
    """See `DEFAULT_BASH_RISK_CLASSIFIER_E2E_TIMEOUT_SECONDS`."""
    bash_risk_classifier_too_risky_threshold: int = DEFAULT_BASH_RISK_CLASSIFIER_TOO_RISKY_THRESHOLD
    """See `DEFAULT_BASH_RISK_CLASSIFIER_TOO_RISKY_THRESHOLD`."""
    bash_risk_classifier_history_size: int = DEFAULT_BASH_RISK_CLASSIFIER_HISTORY_SIZE
    """See `DEFAULT_BASH_RISK_CLASSIFIER_HISTORY_SIZE`."""
    bash_network_enabled: bool = DEFAULT_BASH_NETWORK_ENABLED
    """See `DEFAULT_BASH_NETWORK_ENABLED`."""
    bash_network_recognized_clients: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BASH_NETWORK_RECOGNIZED_CLIENTS))
    """See `DEFAULT_BASH_NETWORK_RECOGNIZED_CLIENTS`. A top-level (not `sessionDefaults`) key
    like every other `tools.bash.*` setting, so — unlike a list-merged `sessionDefaults` key such
    as `readDirs` — a later config layer's value replaces an earlier layer's outright rather than
    concatenating with it, the same 1:1 scalar-replacement `_route_keys()` gives every other
    `PROCESS_KEY_MAP` entry; a project that wants to extend rather than replace the packaged
    default list repeats it in full alongside its own additions."""
    bash_network_socks_port: int = DEFAULT_BASH_NETWORK_SOCKS_PORT
    """See `DEFAULT_BASH_NETWORK_SOCKS_PORT`."""
    bash_network_http_connect_port: int = DEFAULT_BASH_NETWORK_HTTP_CONNECT_PORT
    """See `DEFAULT_BASH_NETWORK_HTTP_CONNECT_PORT`."""
    web_fetch_timeout_seconds: float = DEFAULT_WEB_FETCH_TIMEOUT_SECONDS
    """Maximum wall-clock seconds `WebFetch` lets an HTTP request run before timing out.
    See `tools.webFetch.timeout`."""
    web_fetch_connect_timeout_seconds: float = DEFAULT_WEB_FETCH_CONNECT_TIMEOUT_SECONDS
    """Seconds `WebFetch` waits for a TCP connection before giving up — capped at
    `web_fetch_timeout_seconds`. See `tools.webFetch.connectTimeout`."""
    web_fetch_spill_bytes: int = DEFAULT_WEB_FETCH_SPILL_BYTES
    """Response body byte threshold above which `WebFetch` spills to a file instead of
    returning inline. See `tools.webFetch.spillBytes`."""
    web_fetch_max_body_bytes: int = DEFAULT_WEB_FETCH_MAX_BODY_BYTES
    """Maximum response body bytes `WebFetch` will read, clamped to
    `[1, ABSOLUTE_MAX_BODY_BYTES]`. See `tools.webFetch.maxBodyBytes`."""
    image_default_max_dimension_px: int = DEFAULT_IMAGE_DEFAULT_MAX_DIMENSION_PX
    """Long-edge downscale cap for a model with no `vision_details` resolution bounds. See
    `tools.images.defaultMaxDimensionPx`."""
    image_max_bytes_raw: int = DEFAULT_IMAGE_MAX_BYTES_RAW
    """Post-encode byte ceiling `klorb.images.prepare.prepare_image_for_model` enforces on a
    prepared image. See `tools.images.maxBytesRaw`."""
    image_preferred_formats: list[str] = Field(
        default_factory=lambda: list(DEFAULT_IMAGE_PREFERRED_FORMATS))
    """Transcode preference order `klorb.images.prepare.prepare_image_for_model` tries, in
    order, filtered against the active model's supported mime types. See
    `tools.images.preferredFormats`."""
    subagents_max_depth: int = DEFAULT_SUBAGENTS_MAX_DEPTH
    """See `DEFAULT_SUBAGENTS_MAX_DEPTH` and `tools.subagents.maxDepth`."""
    subagents_max_concurrent_per_parent: int = DEFAULT_SUBAGENTS_MAX_CONCURRENT_PER_PARENT
    """See `DEFAULT_SUBAGENTS_MAX_CONCURRENT_PER_PARENT` and `tools.subagents.maxConcurrentPerParent`."""
    subagents_max_active_total: int = DEFAULT_SUBAGENTS_MAX_ACTIVE_TOTAL
    """See `DEFAULT_SUBAGENTS_MAX_ACTIVE_TOTAL` and `tools.subagents.maxActiveTotal`."""
    session_classifier_model: str | None = None
    """Model `klorb.session_naming.generate_session_name()` sends its request to, via the same
    `ApiProvider` instance the main conversation uses. `None` (the default, and
    `default-config.json`'s own packaged value) means klorb picks the model itself: `klorb.
    session_naming._default_naming_model` looks up a registered model whose `Model.
    klorb_capabilities()` reports `"NANO_CLASSIFIER"` (`klorb.models.registry.ModelRegistry.
    find_by_capability`), falling back to `DEFAULT_SESSION_CLASSIFIER_MODEL` if none is
    registered. Setting this to an explicit model name always wins over that lookup."""
    session_classifier_timeout_seconds: float = DEFAULT_SESSION_CLASSIFIER_TIMEOUT_SECONDS
    """Wall-clock seconds `generate_session_name()`'s request is allowed to take before it's
    treated as a failure (falling back to today's random-nonce session id and no displayed
    title)."""
    session_classifier_e2e_timeout_seconds: float = DEFAULT_SESSION_CLASSIFIER_E2E_TIMEOUT_SECONDS
    """See `DEFAULT_SESSION_CLASSIFIER_E2E_TIMEOUT_SECONDS`."""
    compatibility_claude_markdown: bool = False
    """Whether to read `CLAUDE.md` from the workspace root and fold it into the
    `ProjectGuidance` interjection alongside `AGENTS.md` and `.klorb/INSTRUCTIONS.md` (see
    `Session._build_context_files_interjection`), when the workspace is trusted. A
    compatibility shim for projects that carry Claude-Code-style instructions in a `CLAUDE.md`
    file; `AGENTS.md` and `.klorb/INSTRUCTIONS.md` are always read (once trusted), since
    they're klorb's own conventions."""
    compatibility_claude_skills: bool = False
    """Whether to also discover `workspace`-namespace skills from `${workspace_root}/.claude/skills/`
    (alongside `.klorb/skills/`) when the workspace is trusted — a compatibility shim mirroring
    `compatibility_claude_markdown`. See docs/specs/skills.md."""
    log_tool_calls: bool | None = None
    """Whether to append an out-of-band, file-based record of every tool call to
    `tool-calls.log` under `KLORB_STATE_DIR` — see `klorb.tool_call_log`. Also
    activated by the `LOG_TOOL_CALLS` environment variable (`"1"`/`"true"`) or the
    `--log-tool-calls`/`--no-log-tool-calls` CLI flag pair, only when this field is
    still `None` — see `klorb.tool_call_log.tool_call_logging_enabled`."""
    theme: str | None = None
    """Name of the Textual theme selected via the TUI's theme picker (see
    `klorb.tui.commands.theme_commands`), persisted to the per-user config file under `THEME_CONFIG_KEY`
    so it's restored on the next klorb session. `None` (the default) means no persisted choice
    exists yet; `ReplApp` falls back to Textual's own built-in default theme in that case."""
    sidebar: str | None = None
    """Which sidebar panel is shown by default when klorb starts: ``"tasks"`` for the task
    sidebar (Ctrl+T), ``"agents"`` for the subagents panel (Ctrl+G), or ``None`` (the default)
    for none. Persisted to the per-user config file under `SIDEBAR_CONFIG_KEY` so it's restored
    on the next klorb session. Toggling either panel updates this setting for future sessions."""
    hooks: dict[str, list[HookConfig]] = Field(default_factory=dict)
    """Handler lists keyed by hook name (`onProcessStart`, `onSessionStart`, ...), merged
    across the config-layer stack by named-list concatenation — a later layer's handlers for a
    given hook name are appended to, never replace, an earlier layer's for that same name.
    Empty by default, so hooks are inert unless explicitly configured. See
    `klorb.hooks.config.HOOK_NAMES`."""
    events: dict[str, list[EventConfig]] = Field(default_factory=dict)
    """Handler lists keyed by event name (`FileSystemModified`, `Timer`,
    `WorkspaceTrustChanged`), merged the same named-list-concatenate way as `hooks`. Each
    list's entries are the `EventConfig` subclass `klorb.hooks.config.EVENT_CONFIG_MODELS`
    maps that event name to. Empty by default."""
    config_warnings: list[str] = Field(default_factory=list)
    """Human-readable messages describing any config layer `load_process_config()` had to skip
    over while assembling this `ProcessConfig` — today, only a layer whose file isn't valid
    JSON at all (see `klorb.schema_envelope.parse_versioned_json`'s `warnings` parameter).
    `klorb.tui.ReplApp` posts each of these to the history scroll at startup (and again for
    any newly-discovered ones after `_apply_workspace_config`'s reload), since a `logger.error`
    call alone is easy for an interactive user to miss entirely."""
    argv: list[str] = Field(default_factory=list)
    """The verbatim `argv` the process was given (as observed by `klorb.cli.main()`), kept so a
    later step can re-derive the session-scoped CLI overrides without re-parsing. See
    `session_cli_flags` for the already-derived overrides themselves."""
    session_cli_flags: dict[str, Any] = Field(default_factory=dict)
    """Session-scoped overrides derived from the CLI flags `main()` parsed, keyed by
    `SessionConfig` attribute name (`"interactive"`, `"permission_framework"`,
    `"max_tool_calls_per_turn"`). Populated by
    `klorb.cli.main()` and applied to `session` via `apply_cli_flags_to_session()`; a later
    `clear_session()` re-reads config layers from disk and re-applies these on top, so a CLI
    flag like `--max-tool-calls-per-turn` survives a `/clear` (which otherwise reverts to the
    disk config) the same way it survived the initial startup. Process-only CLI overrides
    (`log_tool_calls`) are applied directly to their `ProcessConfig` field instead, since
    they outlive any one session."""


def apply_cli_flags_to_session(process_config: "ProcessConfig") -> None:
    """Apply `process_config.session_cli_flags` onto `process_config.session` in place.

    Each `(attr_name, value)` pair in `session_cli_flags` is `setattr`'d directly onto the nested
    `SessionConfig`, so the session sees the override immediately. Called by
    `klorb.cli.main()` after assembling `session_cli_flags` and by `ReplApp.clear_session()` after
    re-reading config layers from disk, so the CLI overrides win over the disk config both at
    startup and after a `/clear`.
    """
    for attr_name, value in process_config.session_cli_flags.items():
        setattr(process_config.session, attr_name, value)


@dataclass
class LoadedConfigLayer:
    """One config layer's parsed contents alongside the raw source text and a human-readable
    label identifying where it came from — bundled together (rather than returned as a bare
    tuple) because `_expand_config_layer_macros` needs all three: `contents` to expand and
    merge, `text` to locate a malformed `${...}` reference's line/column within, and
    `source_label` to name the layer in the resulting warning.
    """

    contents: dict[str, Any]
    text: str
    source_label: str


_DIR_RULE_KEYS = ("readDirs", "writeDirs")
_FILE_RULE_KEYS = ("readFiles", "writeFiles")
"""`sessionDefaults` keys `_expand_config_layer_macros` expands `${...}` macros within.
`_FILE_RULE_KEYS` entries are expanded with `forbid_char="*"` (see `expand_macros`) since,
unlike a directory rule, a file rule's matching semantics change if `*` appears in it — see
docs/adrs/00175-file-rule-macro-values-may-not-contain-a-literal-star.md."""


def _expand_rule_block_macros(
    session_layer: dict[str, Any], key: str, macros: dict[str, str], *, forbid_char: str | None,
) -> None:
    """Expand `${...}` macros in every `deny`/`ask`/`allow` string entry of
    `session_layer[key]` (a `readDirs`/`writeDirs`/`readFiles`/`writeFiles`-shaped block), in
    place. A non-string entry (already-malformed config data) is left untouched, so it fails
    later exactly as it did before this function existed, rather than gaining a new error path
    here."""
    rule_block = session_layer.get(key)
    if not isinstance(rule_block, dict):
        return
    for category in ("deny", "ask", "allow"):
        entries = rule_block.get(category)
        if not isinstance(entries, list):
            continue
        rule_block[category] = [
            expand_macros(entry, macros=macros, forbid_char=forbid_char) if isinstance(
                entry, str) else entry
            for entry in entries
        ]


def _report_macro_error(
    exc: MacroExpansionError, *, source_label: str, source_text: str, warnings: list[str],
) -> None:
    """Log and collect (into `warnings`, see `ProcessConfig.config_warnings`) a human-readable
    description of `exc`, formatted exactly like a JSON syntax error (see
    `klorb.schema_envelope.parse_versioned_json`): `source_label`, the error, and a
    line/column-annotated excerpt of `source_text`.

    `exc.snippet` is searched for verbatim in `source_text` to recover a position — this always
    succeeds for a real config file, since a macro token uses only ASCII identifier characters
    and `{`/`}`/`$`, none of which JSON string escaping ever alters. If it somehow isn't found
    (e.g. `source_text` is empty, as for a layer with no on-disk file), the message is still
    reported, just without a line/column.
    """
    pos = source_text.find(exc.snippet)
    if pos == -1:
        message = f"{source_label}: {exc.message}; ignoring its contents."
    else:
        decode_error = json.JSONDecodeError(exc.message, source_text, pos)
        message = (
            f"{source_label}: {exc.message} (line {decode_error.lineno}, "
            f"column {decode_error.colno}); ignoring its contents.\n"
            f"{format_json_error_context(source_text, decode_error)}"
        )
    logger.error(message)
    warnings.append(message)


def _expand_config_layer_macros(
    layer: LoadedConfigLayer, *, workspace_root: Path, warnings: list[str],
) -> dict[str, Any]:
    """Expand every `${home}`/`${workspaceRoot}` macro reference in `layer`'s
    `readDirs`/`writeDirs`/`readFiles`/`writeFiles` rule paths and `setEnv` values, in a single
    left-to-right pass per string (see `klorb.config_macros.expand_macros`) — never a
    per-macro-name sequential replace, which would let one macro's expanded value be
    re-scanned and partially re-expanded by another.

    A malformed or unrecognized macro reference anywhere in `layer` drops the entire layer —
    returns `{}`, exactly as `klorb.schema_envelope.parse_versioned_json` does for a layer
    that isn't valid JSON at all — rather than silently applying the rest of the layer around
    the bad reference. See docs/adrs/00176-malformed-config-macro-drops-the-whole-layer.md: a
    `deny` rule whose macro silently failed to expand (left as literal text, or blanked to an
    empty string) would silently stop matching anything, which is far more dangerous than
    refusing to start with a clear error.
    """
    session_layer = layer.contents.get(SESSION_DEFAULTS_KEY)
    if not isinstance(session_layer, dict):
        return layer.contents
    macros = resolve_macro_values(workspace_root)
    try:
        for key in _DIR_RULE_KEYS:
            _expand_rule_block_macros(session_layer, key, macros, forbid_char=None)
        for key in _FILE_RULE_KEYS:
            _expand_rule_block_macros(session_layer, key, macros, forbid_char="*")
        set_env = session_layer.get("setEnv")
        if isinstance(set_env, dict):
            for env_key, env_value in set_env.items():
                if isinstance(env_value, str):
                    set_env[env_key] = expand_macros(env_value, macros=macros)
    except MacroExpansionError as exc:
        _report_macro_error(
            exc, source_label=layer.source_label, source_text=layer.text, warnings=warnings)
        return {}
    return layer.contents


def _default_config_layer(warnings: list[str]) -> LoadedConfigLayer:
    """The packaged built-in-defaults layer: `klorb.resources/default-config.json`
    (`DEFAULT_CONFIG_RESOURCE_NAME`), read via `importlib.resources` and parsed the same way
    an on-disk `klorb-config.json` layer is. Unlike every other layer `load_process_config()`
    merges, this one is never missing — it ships inside every klorb install — so it always
    contributes a value for every key it lists, e.g. `sessionDefaults.readDirs.deny`'s
    dotfile denylist, which used to only exist in an uninstalled reference file nobody was
    guaranteed to actually load. See docs/specs/process-and-session-config.md. `warnings`
    collects a message (see `ProcessConfig.config_warnings`) if this packaged file somehow
    isn't valid JSON — a packaging bug, not a user error, but still worth surfacing rather
    than silently starting with no built-in defaults at all.
    """
    text = (
        importlib.resources.files("klorb.resources")
        .joinpath(DEFAULT_CONFIG_RESOURCE_NAME)
        .read_text(encoding="utf-8")
    )
    source = f"klorb.resources/{DEFAULT_CONFIG_RESOURCE_NAME}"
    contents = parse_versioned_json(
        text, expected_schema_name=CONFIG_SCHEMA_NAME, source=source, warnings=warnings)
    return LoadedConfigLayer(contents=contents, text=text, source_label=source)


def _load_config_layer(path: Path, warnings: list[str]) -> LoadedConfigLayer:
    """Read and parse the on-disk config layer at `path`, bundled with its raw text and a
    source label (see `LoadedConfigLayer`) — the single-read counterpart of
    `klorb.schema_envelope.read_versioned_json`, which this mirrors exactly (including the
    missing-file and malformed-JSON handling) except that it also keeps `text` around for
    `_expand_config_layer_macros`'s error reporting.
    """
    if not path.is_file():
        logger.debug("No file at %s; skipping.", path)
        return LoadedConfigLayer(contents={}, text="", source_label=str(path))
    text = path.read_text(encoding="utf-8")
    contents = parse_versioned_json(
        text, expected_schema_name=CONFIG_SCHEMA_NAME, source=str(path), warnings=warnings)
    return LoadedConfigLayer(contents=contents, text=text, source_label=str(path))


def etc_config_path() -> Path:
    """Resolve the `/etc`-scope config file path, honoring the `KLORB_ETC_CONFIG` env var
    override. Resolved lazily (at call time, not import time) so a value supplied via a
    `.env` file — loaded by `klorb.cli.main()`'s `load_dotenv()` call before
    `load_process_config()` runs — is actually honored, unlike a module-level constant
    computed from `os.environ` at import time (before `load_dotenv()` has run). Also used by
    `klorb.klorb_init` to resolve `klorb init --system`'s target file.
    """
    override = os.environ.get(KLORB_ETC_CONFIG_ENV_VAR)
    return Path(override) if override else DEFAULT_ETC_CONFIG_PATH


def user_config_path() -> Path:
    """Per-user config file path, honoring the `KLORB_CONFIG_DIR` env var override. Also used
    by `klorb.permissions.grant` to persist an interactive "always for me" permission grant."""
    return get_klorb_config_dir() / CONFIG_FILENAME


def project_config_path(cwd: Path) -> Path:
    """Per-project config file path, rooted at `cwd`. Also used by `klorb.permissions.grant` to
    persist an interactive "this workspace" permission grant."""
    return cwd / KLORB_PROJECT_DIR_NAME / CONFIG_FILENAME


def persist_sidebar(sidebar: str | None, path: Path | None = None) -> None:
    """Write `sidebar` to the config file at `path` (defaults to `user_config_path()`) under
    `SIDEBAR_CONFIG_KEY`, preserving every other key already in that file untouched. Auto-creates
    the file and its parent directory with a minimal schema envelope if it doesn't exist yet.
    Called by `TaskSidebarMixin.action_toggle_task_sidebar` and
    `SubagentsPanelMixin.action_toggle_subagents_panel` so a sidebar visibility choice survives
    to the next klorb session.
    """
    if path is None:
        path = user_config_path()
    raw = read_versioned_json(path, expected_schema_name=CONFIG_SCHEMA_NAME)
    new_contents = dict(raw)
    new_contents[SIDEBAR_CONFIG_KEY] = sidebar
    write_versioned_json(
        path, new_contents, schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)


def persist_theme(theme_name: str) -> None:
    """Write `theme_name` to the per-user config file (`user_config_path()`) under
    `THEME_CONFIG_KEY`, preserving every other key already in that file untouched. Auto-creates
    the file and its parent directory with a minimal schema envelope if it doesn't exist yet.
    Called by `ReplApp.select_theme()` so a theme chosen via the TUI's theme picker survives to
    the next klorb session.
    """
    path = user_config_path()
    raw = read_versioned_json(path, expected_schema_name=CONFIG_SCHEMA_NAME)
    new_contents = dict(raw)
    new_contents[THEME_CONFIG_KEY] = theme_name
    write_versioned_json(
        path, new_contents, schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)


def persist_session_default(path: Path, on_disk_key: str, value: Any) -> None:
    """Merge `sessionDefaults.<on_disk_key> = value` into the config file at `path`, preserving
    every other key in the file untouched — auto-creating `path` (and its parent directory)
    with a minimal schema envelope if it doesn't exist yet. Used by `klorb.tui.ReplApp` to
    make an interactively-selected setting (e.g. `select_model`) the default the next time
    klorb starts, not just for the rest of the current process — see
    docs/specs/process-and-session-config.md.
    """
    raw = read_versioned_json(path, expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = dict(raw.get(SESSION_DEFAULTS_KEY, {}))
    session_defaults[on_disk_key] = value
    new_contents = dict(raw)
    new_contents[SESSION_DEFAULTS_KEY] = session_defaults
    write_versioned_json(
        path, new_contents, schema_name=CONFIG_SCHEMA_NAME, schema_version=CONFIG_SCHEMA_VERSION)


def _load_saved_session_overrides(cwd: Path) -> dict[str, Any]:
    """Return config overrides carried over from a previously saved session's state.

    Placeholder: `klorb.workspace.session_store.SessionState` (see
    docs/specs/session-persistence.md) is never routed through this layering pipeline, so this
    always returns no overrides. Kept as its own step so its place in the override order (after
    `--config`, before CLI flags) is settled now rather than requiring every caller to be
    revisited later, should a restored session's config ever need to flow through here instead
    of replacing the live `SessionConfig` outright the way `SessionPersistenceMixin`/
    `klorb.session.restore.try_restore_session` do today.
    """
    del cwd  # unused until saved-session config layering exists
    return {}


def _route_keys(data: dict[str, Any], key_map: dict[str, str]) -> dict[str, Any]:
    """Translate `data`'s on-disk keys to their internal attribute names via `key_map`,
    dropping (and logging a warning for) any key absent from it — almost certainly a typo
    rather than an intentionally-unrecognized setting.
    """
    routed: dict[str, Any] = {}
    for key, value in data.items():
        attr = key_map.get(key)
        if attr is None:
            logger.warning("Unrecognized klorb-config.json key %r; ignoring.", key)
            continue
        routed[attr] = value
    return routed


def _reverse_key_map(key_map: dict[str, str]) -> dict[str, str]:
    """Reverse a key map: from `{on_disk_key: attr_name}` to `{attr_name: on_disk_key}`."""
    return {attr: key for key, attr in key_map.items()}


# Pre-computed reverse maps for `process_config_to_disk_dict`.
_PROCESS_ATTR_TO_KEY: dict[str, str] = _reverse_key_map(PROCESS_KEY_MAP)
_SESSION_ATTR_TO_KEY: dict[str, str] = _reverse_key_map(SESSION_KEY_MAP)


def process_config_to_disk_dict(process_config: ProcessConfig) -> dict[str, Any]:
    """Convert a `ProcessConfig` back to the on-disk config dict format.

    Only includes keys that are readable from config files -- the keys in
    `SESSION_KEY_MAP`, `PROCESS_KEY_MAP`, and the permission-rule keys like `readDirs` and
    `bashDomains`. Output keys use dotted camelCase names matching the on-disk
    `klorb-config.json` format.

    Permission-rule lists are serialized using the same format the grant
    machinery uses when writing back to config files.
    """
    from klorb.permissions.skill_access import format_fqsn

    result: dict[str, Any] = {}

    # Top-level process config keys.
    for attr_name, on_disk_key in _PROCESS_ATTR_TO_KEY.items():
        value = getattr(process_config, attr_name, None)
        result[on_disk_key] = value

    # hooks/events hold pydantic model instances, not plain JSON-serializable values, so
    # they're re-serialized here rather than left as the raw `getattr` result above.
    result["hooks"] = {
        name: [handler.model_dump(exclude_none=True, by_alias=True) for handler in handlers]
        for name, handlers in process_config.hooks.items()
    }
    result["events"] = {
        name: [handler.model_dump(exclude_none=True, by_alias=True) for handler in handlers]
        for name, handlers in process_config.events.items()
    }

    # Session defaults.
    session: dict[str, Any] = {}
    for attr_name, on_disk_key in _SESSION_ATTR_TO_KEY.items():
        value = getattr(process_config.session, attr_name, None)
        session[on_disk_key] = value

    # Permission rules -- serialized back to on-disk format.
    read_dirs = process_config.session.read_dirs
    session["readDirs"] = {
        "deny": [str(p) for p in read_dirs.deny],
        "ask": [str(p) for p in read_dirs.ask],
        "allow": [str(p) for p in read_dirs.allow],
    }
    write_dirs = process_config.session.write_dirs
    session["writeDirs"] = {
        "deny": [str(p) for p in write_dirs.deny],
        "ask": [str(p) for p in write_dirs.ask],
        "allow": [str(p) for p in write_dirs.allow],
    }
    read_files = process_config.session.read_files
    session["readFiles"] = {
        "deny": [str(p) for p in read_files.deny],
        "ask": [str(p) for p in read_files.ask],
        "allow": [str(p) for p in read_files.allow],
    }
    write_files = process_config.session.write_files
    session["writeFiles"] = {
        "deny": [str(p) for p in write_files.deny],
        "ask": [str(p) for p in write_files.ask],
        "allow": [str(p) for p in write_files.allow],
    }
    command_rules = process_config.session.command_rules
    session["commandRules"] = {
        "deny": [list(p) for p in command_rules.deny],
        "ask": [list(p) for p in command_rules.ask],
        "allow": [list(p) for p in command_rules.allow],
    }
    skill_rules = process_config.session.skill_rules
    session["skillRules"] = {
        "deny": [format_fqsn(pair) for pair in skill_rules.deny],
        "ask": [format_fqsn(pair) for pair in skill_rules.ask],
        "allow": [format_fqsn(pair) for pair in skill_rules.allow],
    }
    web_domain_rules = process_config.session.web_domain_rules
    session["webDomains"] = {
        "deny": list(web_domain_rules.deny),
        "ask": list(web_domain_rules.ask),
        "allow": list(web_domain_rules.allow),
    }
    bash_domain_rules = process_config.session.bash_domain_rules
    session["bashDomains"] = {
        "deny": list(bash_domain_rules.deny),
        "ask": list(bash_domain_rules.ask),
        "allow": list(bash_domain_rules.allow),
    }
    session["shareEnv"] = process_config.session.share_env
    session["setEnv"] = process_config.session.set_env

    if session:
        result["sessionDefaults"] = session

    return result


def _merge_named_handlers_layer(
    raw_layer: dict[str, Any], *, model_for_name: Callable[[str], type[BaseModel] | None],
    accumulator: dict[str, list[Any]], source_label: str, warnings: list[str],
) -> None:
    """Parse and fold one config layer's raw `hooks`/`events` object into `accumulator`
    (`concatenated_hooks`/`concatenated_events` in `load_process_config`), via
    `klorb.hooks.merge`'s named-list-concatenate merge. `model_for_name` resolves a hook/event
    name to the pydantic model its handler-list entries validate against, returning `None` for
    a name `load_process_config` doesn't recognize — collected into `warnings` (see
    `ProcessConfig.config_warnings`) and skipped, the same as an invalid individual entry.
    """
    for name, raw_handlers in raw_layer.items():
        model = model_for_name(name)
        if model is None:
            message = f"{source_label}: unrecognized hook/event name {name!r}; ignoring."
            logger.warning(message)
            warnings.append(message)
            continue
        parsed, parse_warnings = parse_handler_list(
            raw_handlers, model=model, source_label=f"{source_label} ({name})")
        for warning in parse_warnings:
            logger.warning(warning)
        warnings.extend(parse_warnings)
        if name == "Timer":
            clamp_timer_intervals(
                cast("list[TimerEventConfig]", parsed), source_label=f"{source_label} ({name})",
                warnings=warnings)
        concatenate_named_handler_lists(accumulator, {name: parsed})


def load_process_config(
    *, config_flag_path: Path | None = None, cwd: Path | None = None, workspace: Workspace | None = None,
) -> ProcessConfig:
    """Assemble the `ProcessConfig` for this process.

    Layers config data from, in increasing order of precedence:

    1. The packaged built-in defaults, `klorb.resources/default-config.json`
       (`_default_config_layer()`) — always present, never missing.
    2. `/etc/klorb/klorb-config.json` (or `$KLORB_ETC_CONFIG`, if set).
    3. The per-user config file under `KLORB_CONFIG_DIR` (defaults to `~/.config/klorb`).
    4. The per-project config file under `workspace.path/.klorb/` — skipped entirely, not just
       ignored once read, when `workspace.trusted` is `False` (see below).
    5. The file named by `--config` (`config_flag_path`), if given.
    6. Last-session state overrides (not yet implemented; always empty for now).

    Any layer (1-5) whose file isn't valid JSON at all contributes nothing and is skipped, with
    a message describing the parse failure collected into the returned `ProcessConfig`'s
    `config_warnings` — see `klorb.schema_envelope.parse_versioned_json`'s `warnings` parameter.

    Each layer is one JSON object with process-only settings as flat top-level keys and all
    session-scoped settings nested under a `sessionDefaults` key (see
    docs/specs/process-and-session-config.md). Both the top-level object and the
    `sessionDefaults` object are merged independently across layers — a later layer's keys
    overwrite an earlier layer's within each (`dict.update`, not a deep merge) — so a layer
    can override just one session default without repeating every other one. A missing file
    at layer 2 and below is silently skipped — that's the expected common case, not an error
    — and logged at debug level either way, so a user debugging "why didn't my config apply"
    can see what was and wasn't found. CLI flags (other than `--config` itself) are applied on
    top of the returned `ProcessConfig` by the caller, since only `klorb.cli` knows the
    argparse `Namespace` shape.

    One exception to the "`dict.update`, not a deep merge" rule above:
    `sessionDefaults.readDirs`/`writeDirs`/`readFiles`/`writeFiles`/`commandRules`/`skillRules`/
    `webDomains`/`bashDomains`
    are concatenated across layers instead of replaced — a layer's `deny`/`ask`/`allow` entries
    add to, rather than replace, every earlier layer's — since a stricter rule from any layer must
    never be discardable by a looser rule from another. See docs/specs/permissions.md and
    docs/specs/skills.md.
    `sessionDefaults.shareEnv` is
    likewise concatenated (a layer's env var names add to every earlier layer's);
    `sessionDefaults.setEnv` merges key-by-key instead (a later layer's value for the same key
    replaces an earlier layer's, same as `dict.update` but scoped to this one nested object
    rather than the whole `sessionDefaults` dict) — see
    docs/specs/bash-tool-and-command-permissions.md.

    `workspace` identifies the current project root and whether it's trusted — see
    `klorb.workspace.Workspace` and docs/specs/projects-and-trust.md. When omitted (the common
    case for a caller that hasn't resolved one via `klorb.workspace.TrustManager`, e.g.
    tests constructing a `ProcessConfig` directly), a conservative, unregistered, untrusted
    `Workspace` is synthesized from `klorb.permissions.directory_access.find_workspace_root(cwd)`
    — the same ancestor search for a `.klorb/` directory this function has always used for
    `SessionConfig.workspace`, just now also driving whether layer 4 above is read at all. Layer
    4 is read from `workspace.path`, not `cwd` — the two only differ when `workspace` was
    resolved by `TrustManager.resolve_workspace()` against a registered ancestor project. The
    returned `ProcessConfig.session.workspace` is this same `Workspace` — this is the one
    production code path allowed to set `workspace.trusted` `True`, gated entirely on the
    harness-resolved `Workspace`, never on anything a config file said. `workspace` lives on
    `SessionConfig`, not `ProcessConfig`, since it's a per-session concern — see
    docs/adrs/00060-move-workspace-from-processconfig-to-sessionconfig.md.
    """
    cwd = cwd if cwd is not None else Path.cwd()
    workspace = workspace if workspace is not None else Workspace(path=find_workspace_root(cwd))
    logger.debug(
        "load_process_config: cwd=%s workspace=%s (id=%s, is_project=%s, trusted=%s) "
        "config_flag_path=%s", cwd, workspace.path, workspace.id, workspace.is_project,
        workspace.trusted, config_flag_path)

    config_warnings: list[str] = []
    merged: dict[str, Any] = {}
    merged_session_defaults: dict[str, Any] = {}
    concatenated_read_dirs: dict[str, list[Path]] = {"deny": [], "ask": [], "allow": []}
    concatenated_write_dirs: dict[str, list[Path]] = {"deny": [], "ask": [], "allow": []}
    concatenated_read_files: dict[str, list[Path]] = {"deny": [], "ask": [], "allow": []}
    concatenated_write_files: dict[str, list[Path]] = {"deny": [], "ask": [], "allow": []}
    concatenated_command_rules: dict[str, list[list[str]]] = {"deny": [], "ask": [], "allow": []}
    concatenated_skill_rules: dict[str, list[SkillId]] = {"deny": [], "ask": [], "allow": []}
    concatenated_web_domains: dict[str, list[str]] = {"deny": [], "ask": [], "allow": []}
    concatenated_bash_domains: dict[str, list[str]] = {"deny": [], "ask": [], "allow": []}
    concatenated_share_env: list[str] = []
    merged_set_env: dict[str, str] = {}
    concatenated_hooks: dict[str, list[HookConfig]] = {}
    concatenated_events: dict[str, list[EventConfig]] = {}

    def merge_layer(loaded_layer: LoadedConfigLayer) -> None:
        layer = _expand_config_layer_macros(
            loaded_layer, workspace_root=workspace.path, warnings=config_warnings)
        session_layer = layer.pop(SESSION_DEFAULTS_KEY, None) or {}
        for key, accumulator in (
            ("readDirs", concatenated_read_dirs), ("writeDirs", concatenated_write_dirs),
            ("readFiles", concatenated_read_files), ("writeFiles", concatenated_write_files),
        ):
            layer_dir_rules = session_layer.pop(key, None) or {}
            for category in ("deny", "ask", "allow"):
                accumulator[category].extend(Path(entry) for entry in layer_dir_rules.get(category, []))
        layer_command_rules = session_layer.pop("commandRules", None) or {}
        for category in ("deny", "ask", "allow"):
            concatenated_command_rules[category].extend(layer_command_rules.get(category, []))
        layer_skill_rules = session_layer.pop("skillRules", None) or {}
        for category in ("deny", "ask", "allow"):
            for entry in layer_skill_rules.get(category, []):
                # Each on-disk entry is a "<namespace>:<name>" string; a malformed entry (not a
                # string, or missing the separator) is skipped rather than crashing config load.
                if isinstance(entry, str) and ":" in entry:
                    concatenated_skill_rules[category].append(parse_fqsn(entry))
        layer_web_domains = session_layer.pop("webDomains", None) or {}
        for category in ("deny", "ask", "allow"):
            for entry in layer_web_domains.get(category, []):
                if isinstance(entry, str):
                    concatenated_web_domains[category].append(entry)
        layer_bash_domains = session_layer.pop("bashDomains", None) or {}
        for category in ("deny", "ask", "allow"):
            for entry in layer_bash_domains.get(category, []):
                if isinstance(entry, str):
                    concatenated_bash_domains[category].append(entry)
        concatenated_share_env.extend(session_layer.pop("shareEnv", None) or [])
        merged_set_env.update(session_layer.pop("setEnv", None) or {})
        merged_session_defaults.update(session_layer)
        layer_hooks = layer.pop("hooks", None) or {}
        _merge_named_handlers_layer(
            layer_hooks, model_for_name=lambda name: HookConfig if name in HOOK_NAMES else None,
            accumulator=concatenated_hooks, source_label=loaded_layer.source_label,
            warnings=config_warnings)
        layer_events = layer.pop("events", None) or {}
        _merge_named_handlers_layer(
            layer_events, model_for_name=EVENT_CONFIG_MODELS.get, accumulator=concatenated_events,
            source_label=loaded_layer.source_label, warnings=config_warnings)
        merged.update(layer)

    merge_layer(_default_config_layer(config_warnings))
    merge_layer(_load_config_layer(etc_config_path(), config_warnings))
    merge_layer(_load_config_layer(user_config_path(), config_warnings))
    if workspace.trusted:
        logger.debug(
            "load_process_config: workspace %s is trusted; reading project config layer %s",
            workspace.path, project_config_path(workspace.path))
        merge_layer(_load_config_layer(project_config_path(workspace.path), config_warnings))
    else:
        logger.debug(
            "load_process_config: workspace %s is not trusted; skipping project config layer %s",
            workspace.path, project_config_path(workspace.path))
    if config_flag_path is not None:
        merge_layer(_load_config_layer(config_flag_path, config_warnings))
    merge_layer(LoadedConfigLayer(
        contents=_load_saved_session_overrides(cwd), text="", source_label="saved-session-overrides"))

    session_overrides = _route_keys(merged_session_defaults, SESSION_KEY_MAP)
    session_overrides["read_dirs"] = DirRules(**concatenated_read_dirs)
    session_overrides["write_dirs"] = DirRules(**concatenated_write_dirs)
    session_overrides["read_files"] = FileRules(**concatenated_read_files)
    session_overrides["write_files"] = FileRules(**concatenated_write_files)
    session_overrides["command_rules"] = CommandRules(**concatenated_command_rules)
    session_overrides["skill_rules"] = SkillRules(**concatenated_skill_rules)
    session_overrides["web_domain_rules"] = DomainRules(**concatenated_web_domains)
    session_overrides["bash_domain_rules"] = DomainRules(**concatenated_bash_domains)
    session_overrides["share_env"] = concatenated_share_env
    session_overrides["set_env"] = merged_set_env
    session_overrides["workspace"] = workspace
    process_overrides = _route_keys(merged, PROCESS_KEY_MAP)
    process_overrides["hooks"] = concatenated_hooks
    process_overrides["events"] = concatenated_events
    logger.debug(
        "load_process_config: merged readDirs=%s writeDirs=%s readFiles=%s writeFiles=%s "
        "(deny/ask/allow counts) for workspace %s",
        {category: len(paths) for category, paths in concatenated_read_dirs.items()},
        {category: len(paths) for category, paths in concatenated_write_dirs.items()},
        {category: len(paths) for category, paths in concatenated_read_files.items()},
        {category: len(paths) for category, paths in concatenated_write_files.items()},
        workspace.path)
    return ProcessConfig(
        session=SessionConfig(**session_overrides), config_warnings=config_warnings,
        **process_overrides)
