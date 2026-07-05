# © Copyright 2026 Aaron Kimball
"""Process-wide configuration: settings that live for the lifetime of the klorb process and
are shared by every `Session` created within it (today, one at a time; eventually several
running concurrently). See docs/specs/process-and-session-config.md.
"""

import importlib.resources
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from klorb.openrouter import OPENROUTER_BASE_URL
from klorb.paths import KLORB_CONFIG_DIR
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, DirRules, find_workspace_root
from klorb.schema_envelope import parse_versioned_json
from klorb.schema_envelope import read_versioned_json
from klorb.schema_envelope import write_versioned_json
from klorb.session import THINKING_EFFORT_TOKEN_BUDGETS
from klorb.session import SessionConfig
from klorb.session import ThinkingEffort
from klorb.workspace import Workspace

logger = logging.getLogger(__name__)

DEFAULT_READ_FILE_MAX_LINES = 200
"""`ReadFileTool`'s per-call page size default; the canonical source of this value —
`klorb.tools.read_file` has no constant of its own, it reads `ProcessConfig.read_file_max_lines`
via `ToolSetupContext` at construction time instead."""

DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS = 20
"""`EditFileTool`'s bounded-search radius default (in lines) for locating a drifted
`start_line`/`end_line` hint; the canonical source of this value — `klorb.tools.edit_file` has
no constant of its own, it reads `ProcessConfig.edit_file_drift_search_radius` via
`ToolSetupContext` at construction time instead. Chosen relative to a typical number of
single-line edits an agent makes to one file per turn, not file size; see
docs/adrs/edit-file-tolerates-bounded-line-drift-via-local-candidate-search.md."""

DEFAULT_GREP_MAX_RESULTS = 500
"""`GrepTool`'s per-call match cap default; the canonical source of this value —
`klorb.tools.grep` has no constant of its own, it reads `ProcessConfig.grep_max_results` via
`ToolSetupContext` at construction time instead."""

DEFAULT_FIND_FILE_MAX_RESULTS = 500
"""`FindFileTool`'s per-call match cap default; the canonical source of this value —
`klorb.tools.find_file` has no constant of its own, it reads
`ProcessConfig.find_file_max_results` via `ToolSetupContext` at construction time instead."""

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

DEFAULT_PROMPT_INPUT_MAX_LINES = 12
"""Default max soft-wrapped-line height for the REPL's prompt textarea before it scrolls
instead of growing further; see `ProcessConfig.prompt_input_max_lines`."""

DEFAULT_SHELL_COMMAND = "/bin/bash"
"""Default shell binary a `!`-prefixed REPL command is run through; see
`ProcessConfig.shell_command`. The sole canonical source of this value — `klorb.tui.shell`'s
`UserShellCommand` takes `shell_path` as a required constructor argument rather than
defaulting it itself, so this string isn't duplicated across the two modules."""

SESSION_KEY_MAP: dict[str, str] = {
    "model": "model",
    "thinking.enabled": "thinking_enabled",
    "thinking.effort": "thinking_effort",
    "tools.maxCallsPerTurn": "max_tool_calls_per_turn",
    "tools.maxCallsPerSession": "max_tool_calls_per_session",
}
"""Maps each recognized key inside a `klorb-config.json` file's `sessionDefaults` object to
the `SessionConfig` attribute it sets. `interactive` is deliberately absent: it's always
inferred from CLI flags (`-m`/`--interactive`/`--no-interactive`), never config-file-driven.
`role_name` is likewise deliberately absent: the operating role is set by code (defaulting
to the coordinator role), never by a config file — see
docs/specs/roles-and-system-prompts.md. `readDirs`/`writeDirs` are also deliberately
absent — they're merged by concatenation, not 1:1 scalar replacement, so
`load_process_config()` handles them separately, ahead of `_route_keys()` — see
docs/specs/permissions.md. `workspace` is deliberately absent too: it has no on-disk key at
all, by design — see `SessionConfig.workspace`/docs/specs/projects-and-trust.md — a project
must never be able to grant itself trust via its own config file.
"""

PROCESS_KEY_MAP: dict[str, str] = {
    "thinking.tokenBudgets": "thinking_token_budgets",
    "terminal.input.maxLines": "prompt_input_max_lines",
    "tools.readFile.maxLines": "read_file_max_lines",
    "tools.editFile.driftSearchRadius": "edit_file_drift_search_radius",
    "tools.grep.maxResults": "grep_max_results",
    "tools.findFile.maxResults": "find_file_max_results",
    "providers.openrouter.baseUrl": "openrouter_base_url",
    "shell.command": "shell_command",
    "shell.timeout": "shell_timeout_seconds",
    "compatibility.claudeMarkdown": "compatibility_claude_markdown",
    THEME_CONFIG_KEY: "theme",
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
    edit_file_drift_search_radius: int = DEFAULT_EDIT_FILE_DRIFT_SEARCH_RADIUS
    grep_max_results: int = DEFAULT_GREP_MAX_RESULTS
    find_file_max_results: int = DEFAULT_FIND_FILE_MAX_RESULTS
    openrouter_base_url: str = OPENROUTER_BASE_URL
    shell_command: str = DEFAULT_SHELL_COMMAND
    """Shell binary a `!`-prefixed REPL command is run through, e.g. `/bin/bash` or `/bin/zsh`
    — see `klorb.tui.shell.UserShellCommand`."""
    shell_timeout_seconds: float | None = None
    """Maximum wall-clock seconds a `!`-prefixed REPL command may run before it's killed.
    `None` (the default) means no timeout is enforced."""
    compatibility_claude_markdown: bool = False
    """Whether to read `CLAUDE.md` from the workspace root and inject it into the conversation
    alongside `AGENTS.md` as initial context (see `Session._ensure_context_files_message`).
    A compatibility shim for projects that carry Claude-Code-style instructions in a
    `CLAUDE.md` file; `AGENTS.md` is always read, since it's klorb's own convention."""
    theme: str | None = None
    """Name of the Textual theme selected via the TUI's theme picker (see
    `klorb.tui.theme_commands`), persisted to the per-user config file under `THEME_CONFIG_KEY`
    so it's restored on the next klorb session. `None` (the default) means no persisted choice
    exists yet; `ReplApp` falls back to Textual's own built-in default theme in that case."""


def _default_config_layer() -> dict[str, Any]:
    """The packaged built-in-defaults layer: `klorb.resources/default-config.json`
    (`DEFAULT_CONFIG_RESOURCE_NAME`), read via `importlib.resources` and parsed the same way
    an on-disk `klorb-config.json` layer is. Unlike every other layer `load_process_config()`
    merges, this one is never missing — it ships inside every klorb install — so it always
    contributes a value for every key it lists, e.g. `sessionDefaults.readDirs.deny`'s
    dotfile denylist, which used to only exist in an uninstalled reference file nobody was
    guaranteed to actually load. See docs/specs/process-and-session-config.md.
    """
    text = (
        importlib.resources.files("klorb.resources")
        .joinpath(DEFAULT_CONFIG_RESOURCE_NAME)
        .read_text(encoding="utf-8")
    )
    source = f"klorb.resources/{DEFAULT_CONFIG_RESOURCE_NAME}"
    return parse_versioned_json(text, expected_schema_name=CONFIG_SCHEMA_NAME, source=source)


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
    return KLORB_CONFIG_DIR / CONFIG_FILENAME


def project_config_path(cwd: Path) -> Path:
    """Per-project config file path, rooted at `cwd`. Also used by `klorb.permissions.grant` to
    persist an interactive "this workspace" permission grant."""
    return cwd / KLORB_PROJECT_DIR_NAME / CONFIG_FILENAME


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
    with a minimal schema envelope if it doesn't exist yet. Used by `klorb.tui.repl.ReplApp` to
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


def _load_last_session_overrides(cwd: Path) -> dict[str, Any]:
    """Return config overrides carried over from the previous session's saved state.

    Placeholder for the `last-session.json` state file described in TODO.md, which isn't
    written yet. Always returns no overrides until that feature exists; kept as its own
    step so its place in the override order (after `--config`, before CLI flags) is settled
    now rather than requiring every caller to be revisited later.
    """
    del cwd  # unused until last-session state persistence exists
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
    `sessionDefaults.readDirs`/`writeDirs` are concatenated across layers instead of replaced —
    a layer's `deny`/`ask`/`allow` entries add to, rather than replace, every earlier layer's —
    since a stricter rule from any layer must never be discardable by a looser rule from
    another. See docs/specs/permissions.md.

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
    docs/adrs/move-workspace-from-processconfig-to-sessionconfig.md.
    """
    cwd = cwd if cwd is not None else Path.cwd()
    workspace = workspace if workspace is not None else Workspace(path=find_workspace_root(cwd))

    merged: dict[str, Any] = {}
    merged_session_defaults: dict[str, Any] = {}
    concatenated_read_dirs: dict[str, list[Path]] = {"deny": [], "ask": [], "allow": []}
    concatenated_write_dirs: dict[str, list[Path]] = {"deny": [], "ask": [], "allow": []}

    def merge_layer(layer: dict[str, Any]) -> None:
        session_layer = layer.pop(SESSION_DEFAULTS_KEY, None) or {}
        for key, accumulator in (
            ("readDirs", concatenated_read_dirs), ("writeDirs", concatenated_write_dirs),
        ):
            layer_dir_rules = session_layer.pop(key, None) or {}
            for category in ("deny", "ask", "allow"):
                accumulator[category].extend(Path(entry) for entry in layer_dir_rules.get(category, []))
        merged_session_defaults.update(session_layer)
        merged.update(layer)

    merge_layer(_default_config_layer())
    merge_layer(read_versioned_json(etc_config_path(), expected_schema_name=CONFIG_SCHEMA_NAME))
    merge_layer(read_versioned_json(user_config_path(), expected_schema_name=CONFIG_SCHEMA_NAME))
    if workspace.trusted:
        merge_layer(read_versioned_json(
            project_config_path(workspace.path), expected_schema_name=CONFIG_SCHEMA_NAME))
    if config_flag_path is not None:
        merge_layer(read_versioned_json(config_flag_path, expected_schema_name=CONFIG_SCHEMA_NAME))
    merge_layer(_load_last_session_overrides(cwd))

    session_overrides = _route_keys(merged_session_defaults, SESSION_KEY_MAP)
    session_overrides["read_dirs"] = DirRules(**concatenated_read_dirs)
    session_overrides["write_dirs"] = DirRules(**concatenated_write_dirs)
    session_overrides["workspace"] = workspace
    process_overrides = _route_keys(merged, PROCESS_KEY_MAP)
    return ProcessConfig(session=SessionConfig(**session_overrides), **process_overrides)
