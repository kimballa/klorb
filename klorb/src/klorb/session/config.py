# © Copyright 2026 Aaron Kimball
"""`SessionConfig`: per-`Session` configuration set once at startup from parsed CLI arguments."""

from pathlib import Path

from pydantic import BaseModel, Field

from klorb.openrouter import DEFAULT_MODEL
from klorb.permissions.command_access import CommandRules
from klorb.permissions.directory_access import DirRules
from klorb.permissions.file_access import FileRules
from klorb.permissions.skill_access import SkillRules
from klorb.role import OPERATOR_ROLE_NAME
from klorb.session.constants import (
    DEFAULT_MAX_TOOL_CALLS_PER_SESSION,
    DEFAULT_MAX_TOOL_CALLS_PER_TURN,
    PermissionFramework,
    ThinkingEffort,
)
from klorb.workspace import Workspace


class SessionConfig(BaseModel):
    """Configuration for a `Session`, set once at startup from parsed CLI arguments."""

    model: str = DEFAULT_MODEL
    role_name: str = OPERATOR_ROLE_NAME
    """The operating role this session's agent performs — see `klorb.role`. `Session`
    builds its `Role` object from this name itself, via `klorb.role.get_role()`, so a
    session's `Role` can never disagree with this field. Set by code (this default, or a
    future subagent-spawning call site giving the child session its specialist role) —
    deliberately *not* a recognized `klorb-config.json` key (see
    `klorb.process_config.SESSION_KEY_MAP`), so a config file can't change what kind of
    agent the user is talking to."""
    interactive: bool = True
    thinking_enabled: bool = True
    thinking_effort: ThinkingEffort = "high"
    max_tool_calls_per_turn: int = DEFAULT_MAX_TOOL_CALLS_PER_TURN
    max_tool_calls_per_session: int = DEFAULT_MAX_TOOL_CALLS_PER_SESSION
    workspace: Workspace = Field(default_factory=lambda: Workspace(path=Path.cwd()))
    """Which directory this session considers its project root, whether it's a registered
    project, and whether it's trusted — see `klorb.workspace.Workspace` and
    docs/specs/projects-and-trust.md. Lives on `SessionConfig`, not `ProcessConfig`: multiple
    sessions can run concurrently within one process, each against a different directory (and
    so a different trust decision), which makes workspace identity a per-session concern, not
    a process-wide one — see docs/adrs/move-workspace-from-processconfig-to-sessionconfig.md.

    `workspace.path` is the directory the file-editing tools (`EditFile`, `ReplaceAll`,
    `CreateFile`) are always confined to — see
    `klorb.permissions.workspace.resolve_within_workspace` — a hard, non-config-overridable
    boundary. `ReadFile` gets the same hard boundary unless `workspace.trusted` is set (via the
    interactive workspace-trust flow). Defaults to `Workspace(path=Path.cwd())` for callers
    that construct `SessionConfig` directly (e.g. tests); real runs get an explicit,
    ancestor-searched `path` from `klorb.permissions.directory_access.find_workspace_root()` via
    `load_process_config()`. See docs/adrs/confine-file-tools-to-workspace-root.md and
    docs/specs/permissions.md."""
    read_dirs: DirRules = Field(default_factory=DirRules)
    """`readDirs`-config-driven allow/ask/deny rules `ReadFile` consults — see
    `klorb.permissions.directory_access` and docs/specs/permissions.md. Lives on
    `SessionConfig`, not `ProcessConfig`, because a future "ask" flow will let a user approve a
    rule for the rest of the session — the same reason
    `max_tool_calls_per_turn`/`max_tool_calls_per_session` live here too."""
    write_dirs: DirRules = Field(default_factory=DirRules)
    """`writeDirs`-config-driven allow/ask/deny rules the write tools (`EditFile`,
    `ReplaceAll`, `CreateFile`) consult, together with `read_dirs`, in addition to the hard
    `workspace.path` boundary — see `klorb.permissions.workspace.evaluate_write` and
    docs/specs/permissions.md."""
    read_files: FileRules = Field(default_factory=FileRules)
    """`readFiles`-config-driven allow/ask/deny rules for individual files, consulted by
    `klorb.permissions.workspace.resolve_and_evaluate_read` ahead of, and independently from,
    `read_dirs` and the workspace-root boundary — an exact match here is used as-is, with no
    directory-level fallback. See `klorb.permissions.file_access` and
    docs/specs/permissions.md."""
    write_files: FileRules = Field(default_factory=FileRules)
    """`writeFiles`-config-driven allow/ask/deny rules for individual files, consulted by
    `klorb.permissions.workspace.resolve_and_evaluate_write` ahead of, and independently from,
    `write_dirs` and the workspace-root boundary — see `klorb.permissions.file_access` and
    docs/specs/permissions.md."""
    command_rules: CommandRules = Field(default_factory=CommandRules)
    """`commandRules`-config-driven allow/ask/deny rules `BashTool` consults — see
    `klorb.permissions.command_access` and docs/specs/bash-tool-and-command-permissions.md.
    Lives on `SessionConfig`, not `ProcessConfig`, for the same reason `read_dirs`/`write_dirs`
    do: the interactive "ask" flow lets a user approve a command pattern for the rest of the
    session."""
    skill_rules: SkillRules = Field(default_factory=SkillRules)
    """`skillRules`-config-driven allow/ask/deny rules `ActivateSkill`/`ReadSkillFile` consult,
    matched by exact `(namespace, name)` identity — see `klorb.permissions.skill_access` and
    docs/specs/skills.md. Lives on `SessionConfig` like `command_rules`, so the interactive "ask"
    flow can approve a skill for the rest of the session."""
    share_env: list[str] = Field(default_factory=list)
    """Names of environment variables `BashTool` passes through from the klorb process's own
    environment into the shell command it runs, on top of the always-shared `HOME`/`USER` — see
    `klorb.tools.bash.build_bash_env`. On-disk `shareEnv`, concatenated across config layers
    exactly like `read_dirs`/`write_dirs` (see `klorb.process_config.load_process_config`)."""
    set_env: dict[str, str] = Field(default_factory=dict)
    """Environment variable overrides `BashTool` sets for the shell command it runs, applied
    after `share_env`'s pass-through so they shadow it — see `klorb.tools.bash.build_bash_env`.
    On-disk `setEnv`, merged across config layers with a later layer's value for the same key
    replacing an earlier layer's (see `klorb.process_config.load_process_config`)."""
    permission_framework: PermissionFramework = "ask"
    """How `Session._run_tool_calls` resolves a `PermissionAskRequired` verdict for every
    tool-use approval (today, `readDirs`/`writeDirs` "ask" rules; more approval kinds may
    exist in the future): `"ask"` uses `TurnEventHandlers.on_permission_ask` if the caller
    gave one (e.g. the TUI's modal), else fails closed per call, same as `"deny"`; `"auto"`
    auto-approves every ask with an in-memory-only `"session"`-scope grant, without ever
    invoking `on_permission_ask`; `"deny"` fails closed per call without invoking
    `on_permission_ask` even if one was given. Deliberately absent from
    `klorb.process_config.SESSION_KEY_MAP` — like `interactive`, its default depends on
    whether the session is interactive, resolved explicitly by `klorb.cli.main()` rather
    than a static config default. See docs/specs/permissions.md."""
    approved_scopes: set[str] = Field(default_factory=set)
    """Session-only privilege-escalation scopes the user has interactively approved *this
    session*, via the `EscalatePrivileges` tool (see `klorb.tools.escalate_privileges`).
    Valid scopes are `"workspace"` (lifts the unconditional privileged-path deny on the
    workspace's own `${workspace_root}/.klorb/` directory) and `"homedir"` (lifts the
    privileged-path deny on `KLORB_CONFIG_DIR`, `KLORB_DATA_DIR` and `KLORB_STATE_DIR`). See
    `klorb.permissions.directory_access.is_privileged_path` for how the grants are applied.
    Never persisted to disk: a grant here revokes when the session ends (e.g. a `/clear`
    in the REPL starts a fresh `SessionConfig` with an empty set), and a config file can't
    pre-populate it (it's absent from `SESSION_KEY_MAP`). Lives on `SessionConfig`, not
    `ProcessConfig`, precisely because escalation is session-scoped — a `Session` owns its
    own `SessionConfig`, so the grant dies with it rather than leaking to the next session
    the process starts."""
