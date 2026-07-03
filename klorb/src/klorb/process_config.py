# © Copyright 2026 Aaron Kimball
"""Process-wide configuration: settings that live for the lifetime of the klorb process and
are shared by every `Session` created within it (today, one at a time; eventually several
running concurrently). See docs/specs/process-and-session-config.md.
"""

import logging
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from klorb.openrouter import OPENROUTER_BASE_URL
from klorb.paths import KLORB_CONFIG_DIR
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, DirRules, find_workspace_root
from klorb.schema_envelope import read_versioned_json
from klorb.session import THINKING_EFFORT_TOKEN_BUDGETS
from klorb.session import SessionConfig
from klorb.session import ThinkingEffort

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

CONFIG_SCHEMA_NAME = "klorb-config"
CONFIG_SCHEMA_VERSION = "1.0.0"
CONFIG_FILENAME = "klorb-config.json"

KLORB_ETC_CONFIG_ENV_VAR = "KLORB_ETC_CONFIG"
DEFAULT_ETC_CONFIG_PATH = Path("/etc/klorb") / CONFIG_FILENAME

SESSION_DEFAULTS_KEY = "sessionDefaults"

DEFAULT_PROMPT_INPUT_MAX_LINES = 12
"""Default max soft-wrapped-line height for the REPL's prompt textarea before it scrolls
instead of growing further; see `ProcessConfig.prompt_input_max_lines`."""

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
docs/specs/permissions.md.
"""

PROCESS_KEY_MAP: dict[str, str] = {
    "thinking.tokenBudgets": "thinking_token_budgets",
    "terminal.input.maxLines": "prompt_input_max_lines",
    "tools.readFile.maxLines": "read_file_max_lines",
    "tools.editFile.driftSearchRadius": "edit_file_drift_search_radius",
    "providers.openrouter.baseUrl": "openrouter_base_url",
}
"""Maps each recognized top-level `klorb-config.json` key (outside `sessionDefaults`) to the
process-only `ProcessConfig` attribute it sets. `is_workspace_trusted` is deliberately absent:
it has no on-disk key at all, by design — see `ProcessConfig.is_workspace_trusted`."""

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
    openrouter_base_url: str = OPENROUTER_BASE_URL
    is_workspace_trusted: bool = False
    """Whether `ReadFile` may use `readDirs`/`writeDirs`-table-only confinement (able to reach
    outside `workspace_root`) instead of the same hard workspace-root boundary the write tools
    have — see `klorb.permissions.workspace.resolve_and_evaluate_read`. Deliberately absent
    from `PROCESS_KEY_MAP`/`klorb-config.json`: a project must never be able to grant itself
    trust via its own config file. No code path in this codebase sets this `True` outside of
    tests.

    TODO(aaron): a future, not-yet-built "trust this workspace" flow needs to be the only thing
    that can set this `True` in production — interactively confirming trust once per workspace,
    never via a config file key."""


def _etc_config_path() -> Path:
    """Resolve the `/etc`-scope config file path, honoring the `KLORB_ETC_CONFIG` env var
    override. Resolved lazily (at call time, not import time) so a value supplied via a
    `.env` file — loaded by `klorb.cli.main()`'s `load_dotenv()` call before
    `load_process_config()` runs — is actually honored, unlike a module-level constant
    computed from `os.environ` at import time (before `load_dotenv()` has run).
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


def load_process_config(*, config_flag_path: Path | None = None, cwd: Path | None = None) -> ProcessConfig:
    """Assemble the `ProcessConfig` for this process.

    Layers config data from, in increasing order of precedence:

    1. `/etc/klorb/klorb-config.json` (or `$KLORB_ETC_CONFIG`, if set).
    2. The per-user config file under `KLORB_CONFIG_DIR` (defaults to `~/.config/klorb`).
    3. The per-project config file under `cwd/.klorb/`.
    4. The file named by `--config` (`config_flag_path`), if given.
    5. Last-session state overrides (not yet implemented; always empty for now).

    Each layer is one JSON object with process-only settings as flat top-level keys and all
    session-scoped settings nested under a `sessionDefaults` key (see
    docs/specs/process-and-session-config.md). Both the top-level object and the
    `sessionDefaults` object are merged independently across layers — a later layer's keys
    overwrite an earlier layer's within each (`dict.update`, not a deep merge) — so a layer
    can override just one session default without repeating every other one. A missing file
    at any layer is silently skipped — that's the expected common case, not an error — and
    logged at debug level either way, so a user debugging "why didn't my config apply" can
    see what was and wasn't found. CLI flags (other than `--config` itself) are applied on
    top of the returned `ProcessConfig` by the caller, since only `klorb.cli` knows the
    argparse `Namespace` shape.

    One exception to the "`dict.update`, not a deep merge" rule above:
    `sessionDefaults.readDirs`/`writeDirs` are concatenated across layers instead of replaced —
    a layer's `deny`/`ask`/`allow` entries add to, rather than replace, every earlier layer's —
    since a stricter rule from any layer must never be discardable by a looser rule from
    another. See docs/specs/permissions.md.

    `SessionConfig.workspace_root` is set here too, from `find_workspace_root(cwd)` — an
    ancestor search for the nearest directory with a non-symlinked `.klorb` child, falling back
    to `cwd` itself — rather than a bare `Path.cwd()`.
    """
    cwd = cwd if cwd is not None else Path.cwd()

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

    merge_layer(read_versioned_json(_etc_config_path(), expected_schema_name=CONFIG_SCHEMA_NAME))
    merge_layer(read_versioned_json(user_config_path(), expected_schema_name=CONFIG_SCHEMA_NAME))
    merge_layer(read_versioned_json(project_config_path(cwd), expected_schema_name=CONFIG_SCHEMA_NAME))
    if config_flag_path is not None:
        merge_layer(read_versioned_json(config_flag_path, expected_schema_name=CONFIG_SCHEMA_NAME))
    merge_layer(_load_last_session_overrides(cwd))

    session_overrides = _route_keys(merged_session_defaults, SESSION_KEY_MAP)
    session_overrides["read_dirs"] = DirRules(**concatenated_read_dirs)
    session_overrides["write_dirs"] = DirRules(**concatenated_write_dirs)
    session_overrides["workspace_root"] = find_workspace_root(cwd)
    process_overrides = _route_keys(merged, PROCESS_KEY_MAP)
    return ProcessConfig(session=SessionConfig(**session_overrides), **process_overrides)
