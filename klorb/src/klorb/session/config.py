# © Copyright 2026 Aaron Kimball
"""`SessionConfig`: per-`Session` configuration set once at startup from parsed CLI arguments."""

import threading
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SerializeAsAny,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from klorb.hooks.config import EVENT_CONFIG_MODELS, EventConfig, HookConfig
from klorb.openrouter import DEFAULT_MODEL
from klorb.permissions.command_access import CommandRules
from klorb.permissions.directory_access import DirRules
from klorb.permissions.domain_access import DomainRules
from klorb.permissions.file_access import FileRules
from klorb.permissions.skill_access import SkillId, SkillRules
from klorb.permissions.table import Verdict
from klorb.role import OPERATOR_ROLE_NAME
from klorb.session.constants import (
    DEFAULT_MAX_CHAINED_HOOK_TURNS,
    DEFAULT_MAX_TOOL_CALLS_PER_TURN,
    DEFAULT_MEMORY_DELETE_PERMISSION,
    DEFAULT_MEMORY_WRITE_PERMISSION,
    PermissionFramework,
    ThinkingEffort,
)
from klorb.workspace import Workspace


class PermissionFrameworkState(BaseModel):
    """Mutable holder for a session tree's `permission_framework` value. A plain
    `PermissionFramework` scalar field can't do this: pydantic's `model_copy()` always produces
    an independent copy of a scalar attribute, so cloning a `SessionConfig` for a subagent would
    silently diverge its permission framework from its parent's. Wrapping the value in this
    one-field model instead lets `model_copy()`'s shallow-copy semantics (which preserve, not
    clone, nested model references) do the sharing for free."""

    value: PermissionFramework = "ask"


class _WorkspaceAccessLock:
    """A non-reentrant lock wrapper."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def __enter__(self) -> None:
        self._lock.acquire()

    def __exit__(self, *exc_info: object) -> None:
        self._lock.release()

    def __deepcopy__(self, memo: dict[int, Any]) -> "_WorkspaceAccessLock":
        # threading.Lock can't be deep-copied, so cloning a subagent's SessionConfig off its
        # parent's needs a freshly manufactured one instead.
        return _WorkspaceAccessLock()


class WorkspaceAccess(BaseModel):
    """The `workspace`/`read_dirs`/`write_dirs` triple `SessionConfig.workspace_access` holds as
    one field, always replaced as a whole rather than mutated field-by-field, so a reader's
    single attribute access can never observe a mix of two config generations."""

    model_config = ConfigDict(frozen=True)

    workspace: Workspace = Field(default_factory=lambda: Workspace(path=Path.cwd()))
    read_dirs: DirRules = Field(default_factory=DirRules)
    write_dirs: DirRules = Field(default_factory=DirRules)


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
    max_chained_hook_turns: int = DEFAULT_MAX_CHAINED_HOOK_TURNS
    """How many turns in a row `Session._deliver_chained_hook_message` may auto-chain on
    behalf of an `onAgentTurnEnd`/`onSubagentTurnEnd` `chat` handler before refusing further
    ones -- see `DEFAULT_MAX_CHAINED_HOOK_TURNS`. `0` disables chaining; negative means
    unlimited."""
    workspace_access: WorkspaceAccess = Field(default_factory=WorkspaceAccess)
    """Which directory this session considers its project root, whether it's a registered
    project and whether it's trusted, plus the `readDirs`/`writeDirs`-config-driven allow/ask/
    deny rules `ReadFile`/the write tools consult against it. Lives on `SessionConfig`, not
    `ProcessConfig`: multiple sessions can run concurrently within one process, each against a
    different directory with its own trust decision and its own interactively-approved directory
    grants. Read via the `workspace`/`read_dirs`/`write_dirs` properties below, or
    `workspace_access_snapshot()` for all three together; written only via
    `apply_workspace_access()`. `workspace.path` is a hard, non-config-overridable boundary the
    file-editing tools (`EditFile`, `ReplaceAll`, `CreateFile`) are always confined to; `ReadFile`
    gets the same hard boundary unless `workspace.trusted` is set."""
    read_files: FileRules = Field(default_factory=FileRules)
    """`readFiles`-config-driven allow/ask/deny rules for individual files, consulted by
    `klorb.permissions.workspace.resolve_and_evaluate_read` ahead of, and independently from,
    `read_dirs` and the workspace-root boundary — an exact match here is used as-is, with no
    directory-level fallback. See `klorb.permissions.file_access`."""
    write_files: FileRules = Field(default_factory=FileRules)
    """`writeFiles`-config-driven allow/ask/deny rules for individual files, consulted by
    `klorb.permissions.workspace.resolve_and_evaluate_write` ahead of, and independently from,
    `write_dirs` and the workspace-root boundary — see `klorb.permissions.file_access`."""
    command_rules: CommandRules = Field(default_factory=CommandRules)
    """`commandRules`-config-driven allow/ask/deny rules `BashTool` consults — see
    `klorb.permissions.command_access`.
    Lives on `SessionConfig`, not `ProcessConfig`, for the same reason `read_dirs`/`write_dirs`
    do: the interactive "ask" flow lets a user approve a command pattern for the rest of the
    session."""
    skill_rules: SkillRules = Field(default_factory=SkillRules)
    """`skillRules`-config-driven allow/ask/deny rules `ActivateSkill`/`ReadSkillFile` consult,
    matched by exact `(namespace, name)` identity — see `klorb.permissions.skill_access`.
    Lives on `SessionConfig` like `command_rules`, so the interactive "ask"
    flow can approve a skill for the rest of the session."""
    web_domain_rules: DomainRules = Field(default_factory=DomainRules)
    """`webDomains`-config-driven allow/ask/deny rules `WebFetch` consults before any network
    call, matched by exact domain, wildcard prefix (``*.example.com``), or wildcard suffix
    (``172.16.*`` for IP addresses) — see `klorb.permissions.domain_access`. Lives on
    `SessionConfig` like `command_rules`, so the interactive "ask" flow can approve a domain
    for the rest of the session. `WebFetch` runs inside klorb's own trust boundary, so this
    table's shipped defaults are broad (localhost, RFC1918 ranges); `bash_domain_rules` is the
    independent, conservative-by-default counterpart for sandboxed `Bash` network egress — see
    that field's own docstring for why the two are not shared."""
    memory_write_permission: Verdict = DEFAULT_MEMORY_WRITE_PERMISSION
    """Governs `CreateMemory`/`EditMemory` for the `workspace` namespace only -- see
    docs/specs/memories.md. Lives on `SessionConfig`, not `ProcessConfig`, like `skill_rules`, so
    the interactive ask flow can approve workspace memory writes for the rest of the session. The
    `global` namespace is never gated by this: a `global`-namespace write is unconditionally
    allowed."""
    memory_delete_permission: Verdict = DEFAULT_MEMORY_DELETE_PERMISSION
    """Governs `ForgetMemory` for the `workspace` namespace only -- same placement rationale and
    `global`-namespace exemption as `memory_write_permission`."""
    bash_domain_rules: DomainRules = Field(default_factory=DomainRules)
    """`bashDomains`-config-driven allow/ask/deny rules the sandboxed `Bash` network-egress proxy
    (`klorb.sandbox.network.ProxyBackend`) and `BashTool._classify`'s pre-flight scanner consult
    before letting a sandboxed command reach a domain — same matching semantics as
    `web_domain_rules`, but a genuinely separate table: a `Bash` command runs inside an
    `--unshare-net` sandbox with no implicit trust in klorb's own network position, so this table
    ships with none of `web_domain_rules`'s loopback/RFC1918 allowances by default. A user who
    needs a sandboxed command to reach `localhost`/the LAN grants it here through the ordinary
    ask/grant flow, not a hardcoded exception."""
    share_env: list[str] = Field(default_factory=list)
    """Names of environment variables `BashTool` passes through from the klorb process's own
    environment into the shell command it runs, on top of the always-shared `HOME`/`USER` — see
    `klorb.tools.bash.build_bash_env`. On-disk `shareEnv`, concatenated across config layers
    exactly like `read_dirs`/`write_dirs`."""
    set_env: dict[str, str] = Field(default_factory=dict)
    """Environment variable overrides `BashTool` sets for the shell command it runs, applied
    after `share_env`'s pass-through so they shadow it — see `klorb.tools.bash.build_bash_env`.
    On-disk `setEnv`, merged across config layers with a later layer's value for the same key
    replacing an earlier layer's."""
    hooks: dict[str, list[HookConfig]] = Field(default_factory=dict)
    """Handler lists keyed by hook name, for every hook name outside
    `klorb.hooks.config.PROCESS_SCOPED_HOOK_NAMES`. Populated from `klorb-config.json`'s
    top-level `hooks`/`sessionDefaults.hooks` keys and skill `metadata.klorb.hooks` grants. A
    subagent inherits only this dict's `is_heritable=True` entries from its parent."""
    events: dict[str, list[SerializeAsAny[EventConfig]]] = Field(default_factory=dict)
    """Handler lists keyed by event name; every event name is session-scoped. Same population
    sources and `is_heritable`-filtered subagent inheritance as `hooks`."""

    @field_validator("events", mode="before")
    @classmethod
    def _resolve_event_subclasses(cls, value: Any) -> Any:
        """Reconstruct each `events` entry as its `EVENT_CONFIG_MODELS` subclass by dict key, so
        a JSON-validated session recovers `watch`/`interval_minutes`/etc. instead of collapsing
        every entry to the base `EventConfig` shape."""
        if not isinstance(value, dict):
            return value
        result: dict[str, Any] = {}
        for name, entries in value.items():
            model = EVENT_CONFIG_MODELS.get(name)
            if model is None or not isinstance(entries, list):
                result[name] = entries
                continue
            result[name] = [
                entry if isinstance(entry, EventConfig) else model.model_validate(entry)
                for entry in entries
            ]
        return result
    granted_skill_hook_event_ids: set[SkillId] = Field(default_factory=set)
    """Which skills' `metadata.klorb.hooks`/`.events` have already been granted into `hooks`/
    `events` this session, so a re-activated skill isn't re-registered. Never persisted."""
    permission_framework_state: PermissionFrameworkState = Field(
        default_factory=PermissionFrameworkState)
    """Holds the effective `permission_framework` value -- see `permission_framework` below
    and `PermissionFrameworkState`'s own docstring for why this is a nested model rather than
    a plain scalar field."""
    approved_scopes: set[str] = Field(default_factory=set)
    """Session-only privilege-escalation scopes the user has interactively approved *this
    session*, via the `EscalatePrivileges` tool (see `klorb.tools.escalate_privileges`).
    Valid scopes are `"workspace"` (lifts the unconditional privileged-path deny on the
    workspace's own `${workspace_root}/.klorb/` directory) and `"homedir"` (lifts the
    privileged-path deny on `KLORB_CONFIG_DIR`, `KLORB_DATA_DIR` and `KLORB_STATE_DIR`). See
    `klorb.permissions.directory_access.is_privileged_path` for how the grants are applied.
    Never persisted to disk: a grant here revokes when the session ends, and a config file can't
    pre-populate it (it's absent from `SESSION_KEY_MAP`). Lives on `SessionConfig`, not
    `ProcessConfig`, precisely because escalation is session-scoped — a `Session` owns its
    own `SessionConfig`, so the grant dies with it rather than leaking to the next session
    the process starts."""

    @property
    def permission_framework(self) -> PermissionFramework:
        """How `Session._run_tool_calls` resolves a `PermissionAskRequired` verdict for every
        tool-use approval (today, `readDirs`/`writeDirs` "ask" rules; more approval kinds may
        exist in the future): `"ask"` uses `TurnEventHandlers.on_permission_ask` if the caller
        gave one, else fails closed per call, same as `"deny"`; `"auto"`
        auto-approves every ask with an in-memory-only `"session"`-scope grant, without ever
        invoking `on_permission_ask`; `"deny"` fails closed per call without invoking
        `on_permission_ask` even if one was given. Deliberately absent from
        `klorb.process_config.SESSION_KEY_MAP` — like `interactive`, its default depends on
        whether the session is interactive, resolved explicitly by `klorb.cli.main()` rather
        than a static config default.

        A thin read/write proxy onto `permission_framework_state.value` -- kept as a
        same-named property (rather than requiring every caller to spell out
        `.permission_framework_state.value`) so assigning or reading `config.
        permission_framework` reaches through to the box every session in this config's tree
        shares, without changing any existing call site's syntax."""
        return self.permission_framework_state.value

    @permission_framework.setter
    def permission_framework(self, value: PermissionFramework) -> None:
        self.permission_framework_state.value = value

    _workspace_access_lock: _WorkspaceAccessLock = PrivateAttr(default_factory=_WorkspaceAccessLock)
    """Guards `workspace_access_snapshot()`/`apply_workspace_access()`."""

    @model_validator(mode="before")
    @classmethod
    def _pack_workspace_access(cls, data: Any) -> Any:
        """Accept `workspace`/`read_dirs`/`write_dirs` as flat constructor kwargs, folding them
        into `workspace_access` before validation, so every existing call site (and any
        `session.json` persisted before `workspace_access` existed) keeps working unchanged."""
        if not isinstance(data, dict):
            return data
        flat_keys = ("workspace", "read_dirs", "write_dirs")
        if not any(key in data for key in flat_keys):
            return data
        data = dict(data)
        access = dict(data.pop("workspace_access", None) or {})
        for key in flat_keys:
            if key in data:
                access[key] = data.pop(key)
        data["workspace_access"] = access
        return data

    @model_serializer(mode="wrap")
    def _flatten_workspace_access(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        """Serialize `workspace_access`'s three fields back to flat top-level `workspace`/
        `read_dirs`/`write_dirs` keys, so persisted `session.json`/`klorb-config.json` keep the
        on-disk shape they had before `workspace_access` existed."""
        dumped: dict[str, Any] = handler(self)
        access = dumped.pop("workspace_access")
        dumped["workspace"] = access["workspace"]
        dumped["read_dirs"] = access["read_dirs"]
        dumped["write_dirs"] = access["write_dirs"]
        return dumped

    def __eq__(self, other: object) -> bool:
        """Compares field values only, unlike pydantic's default which also compares private
        attributes and would spuriously call two configs unequal over their distinct
        `_workspace_access_lock` instances."""
        if not isinstance(other, SessionConfig):
            return NotImplemented
        return type(self) is type(other) and self.__dict__ == other.__dict__

    @property
    def workspace(self) -> Workspace:
        """Which directory this session considers its project root, whether it's a registered
        project, and whether it's trusted. Grab `workspace_access_snapshot()` instead of this
        property if you need it together with `read_dirs`/`write_dirs`, so all three come from
        the same config generation."""
        return self.workspace_access.workspace

    @property
    def read_dirs(self) -> DirRules:
        """`readDirs`-config-driven allow/ask/deny rules `ReadFile` consults. Grab
        `workspace_access_snapshot()` instead of this property if you need it together with
        `workspace`/`write_dirs`, so all three come from the same config generation."""
        return self.workspace_access.read_dirs

    @property
    def write_dirs(self) -> DirRules:
        """`writeDirs`-config-driven allow/ask/deny rules the write tools consult. Grab
        `workspace_access_snapshot()` instead of this property if you need it together with
        `workspace`/`read_dirs`, so all three come from the same config generation."""
        return self.workspace_access.write_dirs

    def workspace_access_snapshot(self) -> WorkspaceAccess:
        """Read `workspace`/`read_dirs`/`write_dirs` together as one self-consistent triple."""
        with self._workspace_access_lock:
            return self.workspace_access

    def apply_workspace_access(
        self, *, workspace: Workspace, read_dirs: DirRules, write_dirs: DirRules,
    ) -> None:
        """Publish a new `workspace`/`read_dirs`/`write_dirs` triple as one atomic group, so a
        concurrent `workspace_access_snapshot()` reader never observes a mix of old and new
        values."""
        with self._workspace_access_lock:
            self.workspace_access = WorkspaceAccess(
                workspace=workspace, read_dirs=read_dirs, write_dirs=write_dirs)
