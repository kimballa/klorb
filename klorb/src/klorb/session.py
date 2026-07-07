# © Copyright 2026 Aaron Kimball
"""Session state shared by the one-shot prompt path and the interactive REPL."""

import json
import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from coolname import generate_slug
from pydantic import BaseModel, ConfigDict, Field

from klorb.api_provider import ApiProvider, ProviderResponse, ResponseAborted
from klorb.message import Message, ToolCallRequest
from klorb.models.model import Model
from klorb.models.registry import ModelRegistry
from klorb.openrouter import DEFAULT_MODEL, OpenRouterApiProvider
from klorb.permissions.command_access import CommandRules
from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.role import COORDINATOR_ROLE_NAME, Role, get_role
from klorb.system_prompts import DEFAULT_SYS_FILENAME, DEFAULT_SYSTEM_PROMPT, resolve_prompt_file
from klorb.workspace import Workspace

if TYPE_CHECKING:
    # isort: off
    # `ToolRegistry` (via `ToolSetupContext`) depends on `ProcessConfig`, which itself
    # depends on `SessionConfig` from this module — importing it for real here would be
    # circular. Session only stores and calls methods on a `ToolRegistry` it's handed, so a
    # type-checking-only import is enough (see
    # docs/adrs/tool-setup-context-carries-process-and-session-config.md).
    from klorb.tools.registry import ToolRegistry
    # `ProcessConfig` depends on `SessionConfig`/`ThinkingEffort`/`THINKING_EFFORT_TOKEN_BUDGETS`
    # from this module, so importing it for real here would be circular too. `Session` stores
    # (and reads a couple of fields off) a `ProcessConfig` it's handed, but never constructs one
    # itself, so a type-checking-only import is enough, same as `ToolRegistry` above.
    from klorb.process_config import ProcessConfig
    # isort: on

logger = logging.getLogger(__name__)

MAX_TOOL_CALL_ROUNDS = 200
"""Safety cap on how many model-to-tool round trips one turn will run before giving up, in
case a model gets stuck repeatedly requesting tool calls without ever returning a final
answer. Unlike `SessionConfig.max_tool_calls_per_turn`/`max_tool_calls_per_session`, this
isn't user-configurable or interactively raisable — it's a hard backstop."""

DEFAULT_MAX_TOOL_CALLS_PER_TURN = 50
"""Default value of `SessionConfig.max_tool_calls_per_turn`: how many individual tool calls
(across every round) one turn will execute before asking the user whether to keep going (see
`Session._confirm_limit_increase`)."""

DEFAULT_MAX_TOOL_CALLS_PER_SESSION = 200
"""Default value of `SessionConfig.max_tool_calls_per_session`: how many individual tool
calls a `Session` will execute across its entire lifetime (every turn combined) before asking
the user whether to keep going (see `Session._confirm_limit_increase`)."""


class ToolCallLimitExceeded(Exception):
    """Raised when a turn exceeds one of the tool-calling safety caps without the model
    returning a final answer: more than `MAX_TOOL_CALL_ROUNDS` model-to-tool round trips, or
    the user declined to raise `max_tool_calls_per_turn`/`max_tool_calls_per_session` past
    where it was exceeded (see `Session._confirm_limit_increase`)."""


# Two-word kebab-case nonce (e.g. "dastardly-happy") to disambiguate session ids
# created within the same minute.
NONCE_WORD_COUNT = 2

SESSION_ID_TIMESTAMP_FORMAT = "%Y-%m-%d-%H-%M"

ThinkingEffort = Literal["low", "medium", "high"]
"""User-facing thinking depth knob, independent of whether the active model wants an
effort keyword or a numeric token budget (see `klorb.models.model.ThinkingBudgetStyle`)."""

PermissionFramework = Literal["ask", "auto", "deny"]
"""How `Session._run_tool_calls` resolves a `PermissionAskRequired` verdict — see
`SessionConfig.permission_framework` and docs/specs/permissions.md's "Interactive 'ask'
confirmation" section."""

THINKING_EFFORT_TOKEN_BUDGETS: dict[ThinkingEffort, int] = {
    "low": 4_096,
    "medium": 16_384,
    "high": 32_768,
}
"""Default reasoning token budgets used for `ThinkingBudgetStyle == "tokens"` models, keyed
by the same `ThinkingEffort` levels offered for `"effort"`-style models, so both kinds of
model share one user-facing knob. See [[map-thinking-effort-levels-to-fixed-token-budgets]].
Overridable per process via `ProcessConfig.thinking_token_budgets`
(see [[process-and-session-config]]); `Session` falls back to this default when none is
given."""


def generate_session_id() -> str:
    """Generate a session id: yyyy-mm-dd-hh-mm-<nonce>, e.g. '2026-06-30-10-00-happy-otter'."""
    timestamp = datetime.now().strftime(SESSION_ID_TIMESTAMP_FORMAT)
    nonce = generate_slug(NONCE_WORD_COUNT)
    return f"{timestamp}-{nonce}"


def _format_tool_response_content(result: Any, error: str | None) -> str:
    """Render a tool call's `(result, error)` pair (see `ToolCallEvent`) as the string stored
    in its `role="tool_response"` `Message.content`: `"Error: {error}"` on failure, otherwise
    `result` unchanged if it's already a string, else its JSON encoding.
    """
    if error is not None:
        return f"Error: {error}"
    return result if isinstance(result, str) else json.dumps(result)


class SessionConfig(BaseModel):
    """Configuration for a `Session`, set once at startup from parsed CLI arguments."""

    model: str = DEFAULT_MODEL
    role_name: str = COORDINATOR_ROLE_NAME
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
    command_rules: CommandRules = Field(default_factory=CommandRules)
    """`commandRules`-config-driven allow/ask/deny rules `BashTool` consults — see
    `klorb.permissions.command_access` and
    docs/plans/ready/004-bash-permissions-and-bash-tool.md. Lives on `SessionConfig`, not
    `ProcessConfig`, for the same reason `read_dirs`/`write_dirs` do: a future "ask" flow may let
    a user approve a command pattern for the rest of the session."""
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


class PermissionAskContext(BaseModel):
    """Passed to `on_permission_ask` when a tool call raises `PermissionAskRequired`: the
    resolved candidate path, whether it was a write (vs. read) interrogation, and a
    human-readable description of the access, for a UI to build its prompt from without
    re-parsing `PermissionAskRequired`'s message string."""

    path: Path
    is_write: bool
    resource_description: str


class PermissionDecision(BaseModel):
    """The user's answer to a `PermissionAskContext` prompt, returned by `on_permission_ask`.

    `"once"`/`"session"`/`"workspace"`/`"homedir"` all mean "retry the call"; for the latter
    three, `Session._retry_after_permission_decision` applies the persistent grant itself (via
    `klorb.permissions.grant.apply_permission_grant`, using `Session`'s own `ProcessConfig`
    reference) before retrying — `on_permission_ask` only needs to ask the user and report their
    choice back, not apply or persist anything itself. `"deny"`/`"other"` both decline;
    `other_text` is only meaningful for `"other"`, and is surfaced to the model alongside the
    generic denial."""

    choice: Literal["once", "session", "workspace", "homedir", "deny", "other"]
    other_text: str | None = None


class ToolCallEvent(BaseModel):
    """Reports one finished tool call to `TurnEventHandlers.on_tool_call`, fired once per call
    from `_run_tool_calls` right after it completes (including after an `on_permission_ask`-
    driven retry). Carries raw data — the parsed call arguments and either the tool's raw
    (non-JSON-stringified) return value or a failure description — rather than pre-rendered
    display strings, so `Session` stays entirely ignorant of how (or whether) a call is
    displayed; a consumer renders `name`/`args`/`result`/`error` itself, e.g. via
    `klorb.tools.tool.Tool.summary()`/`detail_view()`. See
    docs/adrs/render-tool-calls-via-raw-callback-data.md.
    """

    model_config = ConfigDict(frozen=True)

    call_id: str
    name: str
    args: dict[str, Any]
    result: Any = None
    """The tool's raw return value from `apply()` when `error is None`; meaningless (and
    typically `None`) when the call failed."""
    error: str | None = None
    """Human-readable failure description when the call failed, `None` on success — the sole
    success/failure discriminant, since a successful call may legitimately return `None`."""


class TurnEventHandlers(BaseModel):
    """Immutable bundle of the optional callbacks (and cancellation signal) a caller can
    supply for one turn: `on_chunk`/`on_thinking_chunk` (streamed response text),
    `cancel_event` (abort a turn mid-stream), `on_tool_call_limit_reached` (ask whether to
    raise a safety cap), `on_permission_ask` (ask how to resolve an `"ask"` permission
    verdict), and `on_tool_call` (report one finished tool call, for display). Replaces passing
    these as separate keyword arguments through `send_turn()`/`retry_last_turn()`/
    `_dispatch_turn()` and everything they call — a single object here means a future addition
    only touches this class, not every method's signature along the chain. `frozen=True` since
    a `TurnEventHandlers` is built once per turn and never mutated; `arbitrary_types_allowed=True`
    is needed for the `threading.Event` field (`Callable` fields validate natively without it).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    on_chunk: Callable[[str], None] | None = None
    on_thinking_chunk: Callable[[str], None] | None = None
    cancel_event: threading.Event | None = None
    on_tool_call_limit_reached: Callable[[str], bool] | None = None
    on_permission_ask: Callable[[PermissionAskContext], PermissionDecision] | None = None
    on_tool_call: Callable[[ToolCallEvent], None] | None = None


class Session:
    """Holds the state for the active klorb session and runs its turn-based loop.

    Both the one-shot prompt path and the interactive REPL send their prompts through a
    `Session`: each call to `send_turn()` is one turn of that loop, regardless of whether
    it's the only turn (one-shot) or one of many (REPL).
    """

    def __init__(
        self,
        config: SessionConfig,
        provider: ApiProvider | None = None,
        model_registry: ModelRegistry | None = None,
        session_id: str | None = None,
        tool_registry: "ToolRegistry | None" = None,
        process_config: "ProcessConfig | None" = None,
    ) -> None:
        self.config = config
        self.id = session_id or generate_session_id()
        self._role = get_role(config.role_name)
        self._provider = provider or OpenRouterApiProvider()
        self._model_registry = model_registry or ModelRegistry()
        self._process_config = process_config
        """The `ProcessConfig` this session was constructed with, or `None`. Kept around (beyond
        the fields already pulled out of it above) so `_retry_after_permission_decision` can
        thread it into `klorb.permissions.grant.apply_permission_grant` for a `"workspace"`/
        `"homedir"` grant's process-wide ripple — see that method and
        docs/adrs/session-applies-its-own-permission-grants.md."""
        self._thinking_token_budgets = (
            process_config.thinking_token_budgets if process_config is not None
            else THINKING_EFFORT_TOKEN_BUDGETS
        )
        self._tool_registry = tool_registry
        self._tool_calls_this_session = 0
        self._tool_calls_this_turn = 0
        self._compatibility_claude_markdown = (
            process_config.compatibility_claude_markdown if process_config is not None else False
        )
        self._messages: list[Message] = []
        self._context_files_seeded = False
        """Whether `_ensure_context_files_message()` has already inserted the workspace
        context-files (`AGENTS.md`, and `CLAUDE.md` when compatibility is enabled) message
        into `self._messages`. Unlike `_ensure_system_message()`/`_ensure_tool_defs_message()`,
        which can test for an existing `role="system"`/`role="tool_defs"` message, the
        context-files message is `role="user"` (it must look like a real user turn to the
        model), so idempotency is tracked with this flag instead."""

    @property
    def role(self) -> Role:
        """Return the `Role` this session's agent operates as, built (in `__init__`, from
        `config.role_name`) via `klorb.role.get_role()`."""
        return self._role

    @property
    def provider(self) -> ApiProvider:
        """Return the `ApiProvider` this session sends turns through."""
        return self._provider

    @property
    def model_registry(self) -> ModelRegistry:
        """Return the `ModelRegistry` this session resolves model names against."""
        return self._model_registry

    @property
    def tool_registry(self) -> "ToolRegistry | None":
        """Return the `ToolRegistry` this session dispatches model tool-call requests
        through, or `None` if tool-calling isn't enabled for this session."""
        return self._tool_registry

    @property
    def thinking_token_budgets(self) -> dict[ThinkingEffort, int]:
        """Return the reasoning token budgets this session looks up `config.thinking_effort`
        in for `"tokens"`-style models (see `_reasoning_params`).
        """
        return self._thinking_token_budgets

    @property
    def messages(self) -> list[Message]:
        """Return the session's conversation history so far."""
        return list(self._messages)

    def _active_model(self) -> Model | None:
        """Return the registered `Model` for `config.model`, or `None` if it isn't registered."""
        try:
            return self._model_registry.get(self.config.model)
        except KeyError:
            return None

    def active_model_name(self) -> str:
        """Resolve the identifier of the currently configured model.

        Invokes the registered `Model.name()` when `config.model` matches a model
        discovered by the `ModelRegistry`; otherwise returns `config.model` unchanged, so
        callers can still target any OpenRouter model identifier that hasn't been given a
        `Model` implementation yet.
        """
        model = self._active_model()
        return model.name() if model is not None else self.config.model

    def _tokens_recorded_so_far(self) -> int:
        """Sum of `num_tokens` across all messages currently in the buffer."""
        return sum(message.num_tokens for message in self._messages)

    def total_tokens_used(self) -> int:
        """Return the running total of tokens consumed by the conversation so far."""
        return self._tokens_recorded_so_far()

    def max_context_window(self) -> int | None:
        """Return the active model's max context window in tokens, or `None` if the
        active model isn't registered or doesn't report one.
        """
        model = self._active_model()
        if model is None:
            return None
        return model.capabilities().get("max_context_window")

    def _default_system_prompt(self) -> str:
        """Return the role- and model-agnostic default system prompt: the `default_sys.md`
        prompt file (user override tier, then packaged tier — see
        `klorb.system_prompts.resolve_prompt_file`), falling back to the hardcoded
        `DEFAULT_SYSTEM_PROMPT` constant only if that file exists in neither tier (it
        always ships inside the `klorb.resources` package, so the constant is a safety net,
        not an expected path)."""
        prompt = resolve_prompt_file(DEFAULT_SYS_FILENAME)
        return prompt if prompt is not None else DEFAULT_SYSTEM_PROMPT

    def _resolve_system_prompt(self) -> str:
        """Resolve the system prompt for the active turn, most specific source first: the
        session's `Role`'s prompt tiers (role+model, then role default — see
        `Role.system_prompt`), then the active model's role-agnostic prompt
        (`Model.system_prompt`, skipped when `config.model` isn't registered), then
        `_default_system_prompt()`. Never returns an empty prompt: the final tier always
        produces one. Re-derived fresh each place it's needed, so it always reflects the
        *current* active model — see docs/specs/roles-and-system-prompts.md."""
        model = self._active_model()
        prompt = self._role.system_prompt(model)
        if prompt is not None:
            return prompt
        if model is not None:
            prompt = model.system_prompt()
            if prompt is not None:
                return prompt
        return self._default_system_prompt()

    def _reasoning_params(self) -> dict[str, Any] | None:
        """Return the provider-shaped `reasoning` request body for the active turn, or
        `None` if thinking shouldn't be requested (disabled in config, or the active model
        isn't registered or doesn't report `thinking` support). Translates
        `config.thinking_effort` into whichever shape the active model's
        `thinking_budget_style` wants: an `"effort"` keyword, or a numeric `"max_tokens"`
        budget looked up from `THINKING_EFFORT_TOKEN_BUDGETS`.
        """
        if not self.config.thinking_enabled:
            return None
        model = self._active_model()
        if model is None or not model.capabilities().get("thinking", False):
            return None
        budget_style = model.capabilities().get("thinking_budget_style", "effort")
        if budget_style == "tokens":
            return {"max_tokens": self._thinking_token_budgets[self.config.thinking_effort]}
        return {"effort": self.config.thinking_effort}

    def _ensure_system_message(self) -> None:
        """Insert a `role="system"` `Message` at the very front of `self._messages`,
        recording the session's resolved system prompt (see `_resolve_system_prompt` —
        resolution always produces a prompt, so one is always inserted), the first time a
        turn is dispatched. A no-op if one is already present (so retries and later turns
        don't insert a duplicate). This message is bookkeeping only: it's never replayed to
        the model as a chat message (see `OpenRouterApiProvider._build_api_messages`) — the
        live system prompt is sent via `send_prompt(system_prompt=...)` on every turn
        instead (re-resolved fresh via `_resolve_system_prompt()` each time, so it reflects
        the *current* active model even if the session's `config.model` changes after this
        message was inserted), so this doesn't need to be kept in sync with such changes
        after it's first inserted.
        """
        if any(message.role == "system" for message in self._messages):
            return
        system_prompt = self._resolve_system_prompt()
        self._messages.insert(0, Message(
            content=system_prompt,
            role="system",
            num_tokens=0,
            processing_state="complete",
            timestamp=datetime.now(),
        ))

    def _ensure_tool_defs_message(self) -> None:
        """Insert a `role="tool_defs"` `Message` recording the tool definitions offered for
        this session — right after the `role="system"` message if `_ensure_system_message()`
        inserted one, otherwise at the very front of `self._messages` — the first time a turn
        is dispatched with a non-empty `tool_registry`. A no-op if `tool_registry` is
        unset/empty, or if one is already present (so retries and later turns don't insert a
        duplicate). This message is bookkeeping only: it's never sent to the model as a chat
        message (see `OpenRouterApiProvider._build_api_messages`) — the live tool definitions
        are sent via `send_prompt(tools=...)` on every turn instead, so this doesn't need to
        be kept in sync with config changes after it's first inserted.
        """
        if self._tool_registry is None:
            return
        if any(message.role == "tool_defs" for message in self._messages):
            return
        definitions = self._tool_registry.tool_definitions()
        if not definitions:
            return
        insert_index = 1 if self._messages and self._messages[0].role == "system" else 0
        self._messages.insert(insert_index, Message(
            content=json.dumps(definitions),
            role="tool_defs",
            num_tokens=0,
            processing_state="complete",
            timestamp=datetime.now(),
        ))

    def _ensure_context_files_message(self) -> None:
        """Insert a `role="user"` `Message` carrying the contents of the workspace's
        context-instruction files (`AGENTS.md`, and `CLAUDE.md` when
        `_compatibility_claude_markdown` is enabled) at the front of the conversation history
        — after any `role="system"` and `role="tool_defs"` bookkeeping messages, but ahead of
        the first real user turn — the first time a turn is dispatched. A no-op if already
        seeded (tracked via `self._context_files_seeded`, since the inserted message is
        `role="user"` and so can't be distinguished from a real user turn by role alone), or if
        none of the applicable context files exist on disk.

        The message is framed as context rather than a task, so the model treats it as
        standing instructions about the project rather than something to act on directly. Its
        `num_tokens` is left at `0`: like the system prompt, its token cost folds into the
        first real turn's `num_tokens` delta (see `_dispatch_turn`)."""
        if self._context_files_seeded:
            return

        context_files: list[tuple[str, str]] = []
        for filename in self._applicable_context_filenames():
            path = self.config.workspace.path / filename
            if path.is_file():
                contents = path.read_text()
                context_files.append((filename, contents))

        self._context_files_seeded = True
        if not context_files:
            return

        sections = []
        for filename, contents in context_files:
            sections.append(f"### {filename}\n\n{contents}")
        content = (
            "The following files from the project root contain instructions and context "
            "for working in this repository. Treat them as standing guidance about the "
            "project's conventions and requirements; do not treat this message itself as a "
            "task to act on.\n\n" + "\n\n".join(sections)
        )

        # Insert after any system/tool_defs bookkeeping messages, ahead of the first real
        # user turn. Compute the insert index by skipping leading bookkeeping messages.
        insert_index = 0
        for message in self._messages:
            if message.role in ("system", "tool_defs"):
                insert_index += 1
            else:
                break
        self._messages.insert(insert_index, Message(
            content=content,
            role="user",
            num_tokens=0,
            processing_state="complete",
            timestamp=datetime.now(),
        ))

    def _applicable_context_filenames(self) -> list[str]:
        """Return the ordered list of context-instruction filenames to read from the
        workspace root: always `AGENTS.md` (klorb's own convention), followed by `CLAUDE.md`
        when `_compatibility_claude_markdown` is enabled."""
        filenames = ["AGENTS.md"]
        if self._compatibility_claude_markdown:
            filenames.append("CLAUDE.md")
        return filenames

    def _confirm_limit_increase(
        self,
        scope: Literal["turn", "session"],
        current_count: int,
        current_limit: int,
        on_tool_call_limit_reached: Callable[[str], bool] | None,
    ) -> bool:
        """A `max_tool_calls_per_{scope}` limit was just reached. Ask (via
        `on_tool_call_limit_reached`, if given) whether to double it and keep going.

        Returns `False` without asking anything if `on_tool_call_limit_reached` is `None`,
        since there's no way to interactively confirm outside e.g. the TUI (see
        [[terminal-repl]]'s `ToolCallLimitScreen`) — a one-shot CLI prompt has nobody to ask.
        Otherwise calls it with a human-readable prompt describing the limit reached, and:
        on `True`, doubles `self.config.max_tool_calls_per_{scope}` for the rest of this
        `Session`'s lifetime (not just this turn) and returns `True`; on `False`, returns
        `False` without changing the limit.
        """
        logger.warning(
            "%s tool-call limit reached (%d/%d).", scope.capitalize(), current_count, current_limit)
        if on_tool_call_limit_reached is None:
            return False
        prompt = (
            f"This task has made {current_count} tool calls this {scope}, reaching its "
            f"configured limit of {current_limit}. Continue tool use?"
        )
        if not on_tool_call_limit_reached(prompt):
            logger.info("User declined to raise the %s tool-call limit; aborting turn.", scope)
            return False
        new_limit = current_limit * 2
        if scope == "turn":
            self.config.max_tool_calls_per_turn = new_limit
        else:
            self.config.max_tool_calls_per_session = new_limit
        logger.info("User approved continuing; %s tool-call limit doubled to %d.", scope, new_limit)
        return True

    def _retry_after_permission_decision(
        self,
        call: ToolCallRequest,
        args: dict[str, Any],
        ask_exc: PermissionAskRequired,
        decision: PermissionDecision,
    ) -> tuple[Any, str | None]:
        """Resolve a `PermissionAskRequired` into a `(result, error)` pair (see
        `_format_tool_response_content` for how a caller turns this into `tool_response`
        content): for `"session"`/`"workspace"`/`"homedir"`, first applies the grant `decision`
        implies via `klorb.permissions.grant.apply_permission_grant` — passing `self.config` and
        `self._process_config` (possibly `None`; that function skips the process-wide ripple,
        but still persists to disk, in that case) — then retries the call exactly once, same as
        `"once"` (which instead retries via a one-shot `ToolSetupContext.permission_override`,
        persisting nothing). Reports a denial for `"deny"`/`"other"` with no retry. Any exception
        from the retried call (including a second `PermissionAskRequired`) is reported the same
        generic way an ordinary tool failure is — never a second ask.
        """
        assert ask_exc.path is not None
        if decision.choice == "deny":
            return None, f"Permission denied: {ask_exc}"
        if decision.choice == "other":
            return None, f"Permission denied: {ask_exc}. User note: {decision.other_text}"
        if decision.choice in ("session", "workspace", "homedir"):
            # Local import: `klorb.permissions.grant` imports `SessionConfig` from this module,
            # so importing it at module scope here would be circular.
            from klorb.permissions.grant import apply_permission_grant
            apply_permission_grant(
                decision.choice, self.config, self._process_config, ask_exc.path, ask_exc.is_write)
        assert self._tool_registry is not None
        try:
            if decision.choice == "once":
                tool = self._tool_registry.instantiate_tool(call.name, permission_override=ask_exc.path)
            else:
                tool = self._tool_registry.instantiate_tool(call.name)
            return tool.apply(args), None
        except Exception as exc:
            logger.warning("Retried tool call %s(%s) failed: %s", call.name, call.arguments, exc)
            return None, str(exc)

    def _run_tool_calls(
        self,
        tool_use_message: Message,
        callbacks: TurnEventHandlers,
    ) -> None:
        """Execute every tool call requested by `tool_use_message` (see `_send_and_receive`)
        via `tool_registry.instantiate_tool()`, appending one `role="tool_response"` Message
        per call — carrying that call's result, or a description of the error if the tool
        was unknown or raised — so the next round can send it back to the model. Also fires
        `callbacks.on_tool_call`, if given, once per call with a `ToolCallEvent` carrying the
        same result-or-error outcome as raw data, for a UI to render.

        Before executing each call, checks it against `config.max_tool_calls_per_turn`
        (`self._tool_calls_this_turn` resets to `0` at the start of every `_dispatch_turn`)
        and `config.max_tool_calls_per_session` (`self._tool_calls_this_session`; never
        reset, accumulates for the life of this `Session`). Reaching either asks
        `_confirm_limit_increase()` whether to double it and keep going; if that returns
        `False` (declined, or `callbacks.on_tool_call_limit_reached` wasn't given), raises
        `ToolCallLimitExceeded` without executing this call (or any later call in
        `tool_use_message.tool_calls`).

        If a call raises `PermissionAskRequired` with a `path`, how it's resolved depends on
        `config.permission_framework` (see `SessionConfig.permission_framework`): `"deny"` fails
        closed without invoking any callback; `"auto"` auto-approves via a synthesized
        `PermissionDecision(choice="session")`, also without invoking any callback; `"ask"` asks
        `callbacks.on_permission_ask`, if given, for a `PermissionDecision` and retries or denies
        accordingly (see `_retry_after_permission_decision`) — with no callback (e.g. a headless
        one-shot run left at the `"ask"` default), it fails closed the same as `"deny"`. An
        exception with no `path` (a call site that didn't supply one) always fails closed,
        regardless of `permission_framework`. Failing closed reports the error back to the model
        as this call's `tool_response`, exactly like any other tool exception.
        """
        assert self._tool_registry is not None
        assert tool_use_message.tool_calls is not None
        logger.info(
            "Dispatching %d tool call(s) requested by the model: %s",
            len(tool_use_message.tool_calls), [call.name for call in tool_use_message.tool_calls])
        for call in tool_use_message.tool_calls:
            if self._tool_calls_this_turn >= self.config.max_tool_calls_per_turn:
                if not self._confirm_limit_increase(
                    "turn", self._tool_calls_this_turn, self.config.max_tool_calls_per_turn,
                    callbacks.on_tool_call_limit_reached,
                ):
                    raise ToolCallLimitExceeded(
                        f"Exceeded {self.config.max_tool_calls_per_turn} tool call(s) in one turn.")
            if self._tool_calls_this_session >= self.config.max_tool_calls_per_session:
                if not self._confirm_limit_increase(
                    "session", self._tool_calls_this_session, self.config.max_tool_calls_per_session,
                    callbacks.on_tool_call_limit_reached,
                ):
                    raise ToolCallLimitExceeded(
                        f"Exceeded {self.config.max_tool_calls_per_session} tool call(s) in this session.")

            self._tool_calls_this_turn += 1
            self._tool_calls_this_session += 1
            logger.info(
                "Tool call %d/%d this turn, %d/%d this session: %s(%s) [id=%s]",
                self._tool_calls_this_turn, self.config.max_tool_calls_per_turn,
                self._tool_calls_this_session, self.config.max_tool_calls_per_session,
                call.name, call.arguments, call.id,
            )
            args = json.loads(call.arguments) if call.arguments else {}
            result: Any = None
            error: str | None = None
            try:
                tool = self._tool_registry.instantiate_tool(call.name)
                logger.debug("Tool call %s parsed arguments: %r", call.name, args)
                result = tool.apply(args)
                logger.info("Tool call %s succeeded: %s", call.name, result)
            except PermissionAskRequired as ask_exc:
                if ask_exc.path is None or self.config.permission_framework == "deny":
                    logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, ask_exc)
                    error = str(ask_exc)
                elif self.config.permission_framework == "auto":
                    logger.info(
                        "Auto-approving permission ask under permissionFramework=auto: %s", ask_exc)
                    decision = PermissionDecision(choice="session")
                    result, error = self._retry_after_permission_decision(call, args, ask_exc, decision)
                elif callbacks.on_permission_ask is None:
                    logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, ask_exc)
                    error = str(ask_exc)
                else:
                    decision = callbacks.on_permission_ask(PermissionAskContext(
                        path=ask_exc.path, is_write=ask_exc.is_write,
                        resource_description=str(ask_exc)))
                    result, error = self._retry_after_permission_decision(call, args, ask_exc, decision)
            except Exception as exc:
                logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, exc)
                error = str(exc)
            content = _format_tool_response_content(result, error)
            if callbacks.on_tool_call is not None:
                callbacks.on_tool_call(ToolCallEvent(
                    call_id=call.id, name=call.name, args=args, result=result, error=error))
            self._messages.append(Message(
                content=content,
                role="tool_response",
                num_tokens=0,
                processing_state="complete",
                timestamp=datetime.now(),
                tool_call_id=call.id,
            ))
        logger.info(
            "Finished dispatching tool calls (%d/%d this turn, %d/%d this session).",
            self._tool_calls_this_turn, self.config.max_tool_calls_per_turn,
            self._tool_calls_this_session, self.config.max_tool_calls_per_session,
        )

    def _send_and_receive(
        self,
        message_snapshot: list[Message],
        system_prompt: str | None,
        model_name: str,
        reasoning: dict[str, Any] | None,
        tools: list[dict[str, Any]] | None,
        callbacks: TurnEventHandlers,
    ) -> tuple[Message, ProviderResponse]:
        """Send one request to the active model and return `(reply, result)`.

        Streams the reply: on the first chunk, a placeholder `Message`
        (`processing_state="started_receipt"`, `streaming_content=[]`) is appended to
        `self._messages`, and each chunk's text is appended to it. On success, that same
        placeholder is finalized in place (`content`, `streaming_content=None`,
        `processing_state="complete"`, `num_tokens`, `finish_reason`) rather than appending a
        second, duplicate `Message`; if no placeholder was ever created (e.g. a non-streaming
        test double), the provider's reply is appended fresh instead. The finalized message's
        `role` is `"tool_use"` (with `tool_calls` populated) if the model asked to call
        tools, or `"assistant"` otherwise.

        Reasoning/thinking deltas are handled the same way, but as a separate
        `role="thinking"` placeholder appended ahead of the reply one, so the two can be told
        apart and rendered separately. Its `num_tokens` is left at `0`: reasoning tokens are
        already folded into the reply message's `num_tokens` via `result.message.num_tokens`,
        so giving the thinking message its own count would double-count them.

        On any exception other than `ResponseAborted`, whichever placeholder(s) were created
        are mutated to `processing_state="error"`/`last_error` (partial `streaming_content`
        left intact) before re-raising, mirroring how the caller is expected to mark the
        turn's own anchor message (a user message, or a prior round's `tool_response`). On
        `ResponseAborted`, those placeholder(s) are instead finalized in place with
        `processing_state="aborted"` and whatever `streaming_content` had arrived before the
        interruption folded into `content` — the user may still want to read a partial reply,
        and it stays out of the way of a resent prompt since `"aborted"` isn't `"complete"`.
        """
        placeholder: Message | None = None
        thinking_placeholder: Message | None = None

        def handle_chunk(delta_text: str) -> None:
            nonlocal placeholder
            if placeholder is None:
                placeholder = Message(
                    content="",
                    role="assistant",
                    num_tokens=0,
                    processing_state="started_receipt",
                    timestamp=datetime.now(),
                    streaming_content=[],
                )
                self._messages.append(placeholder)
            assert placeholder.streaming_content is not None
            placeholder.streaming_content.append(delta_text)
            if callbacks.on_chunk is not None:
                callbacks.on_chunk(delta_text)

        def handle_thinking_chunk(delta_text: str) -> None:
            nonlocal thinking_placeholder
            if thinking_placeholder is None:
                thinking_placeholder = Message(
                    content="",
                    role="thinking",
                    num_tokens=0,
                    processing_state="started_receipt",
                    timestamp=datetime.now(),
                    streaming_content=[],
                )
                self._messages.append(thinking_placeholder)
            assert thinking_placeholder.streaming_content is not None
            thinking_placeholder.streaming_content.append(delta_text)
            if callbacks.on_thinking_chunk is not None:
                callbacks.on_thinking_chunk(delta_text)

        try:
            result = self._provider.send_prompt(
                message_snapshot, system_prompt=system_prompt, model=model_name,
                session_id=self.id, reasoning=reasoning, tools=tools, on_chunk=handle_chunk,
                on_thinking_chunk=handle_thinking_chunk, cancel_event=callbacks.cancel_event)
        except ResponseAborted:
            if thinking_placeholder is not None:
                thinking_placeholder.content = "".join(thinking_placeholder.streaming_content or [])
                thinking_placeholder.streaming_content = None
                thinking_placeholder.processing_state = "aborted"
            if placeholder is not None:
                placeholder.content = "".join(placeholder.streaming_content or [])
                placeholder.streaming_content = None
                placeholder.processing_state = "aborted"
            raise
        except Exception as exc:
            if placeholder is not None:
                placeholder.processing_state = "error"
                placeholder.last_error = str(exc)
            if thinking_placeholder is not None:
                thinking_placeholder.processing_state = "error"
                thinking_placeholder.last_error = str(exc)
            raise

        if thinking_placeholder is not None:
            thinking_placeholder.content = "".join(thinking_placeholder.streaming_content or [])
            thinking_placeholder.streaming_content = None
            thinking_placeholder.processing_state = "complete"
            thinking_placeholder.last_error = None

        if placeholder is not None:
            placeholder.content = result.message.content
            placeholder.streaming_content = None
            placeholder.processing_state = "complete"
            placeholder.last_error = None
            placeholder.num_tokens = result.message.num_tokens
            placeholder.finish_reason = result.message.finish_reason
            placeholder.tool_calls = result.message.tool_calls
        else:
            placeholder = result.message
            self._messages.append(placeholder)

        if placeholder.tool_calls:
            placeholder.role = "tool_use"

        return placeholder, result

    def _dispatch_turn(
        self,
        user_message: Message,
        tokens_before: int,
        callbacks: TurnEventHandlers | None = None,
    ) -> str:
        """Send `user_message` (already present in `self._messages`) and mutate it in place.

        Delegates the actual request/response streaming to `_send_and_receive()`. If the
        model's reply requests tool calls (`reply.role == "tool_use"`), `_run_tool_calls()`
        dispatches them via `tool_registry` and appends their results as `tool_response`
        messages, then `_send_and_receive()` is called again with the updated history — this
        repeats until the model returns a plain `"assistant"` reply, or `ToolCallLimitExceeded`
        is raised after `MAX_TOOL_CALL_ROUNDS` round trips, `max_tool_calls_per_turn`
        individual tool calls in this turn, or `max_tool_calls_per_session` individual tool
        calls across this `Session`'s lifetime (see `_run_tool_calls`).
        `self._tool_calls_this_turn` is reset to `0` at the start of every call to
        `_dispatch_turn` (including retries); `self._tool_calls_this_session` is never reset.

        On success, `user_message.num_tokens` is set to the delta between the *last* round's
        total `prompt_tokens` and `tokens_before` (the tokens already recorded prior to this
        turn) — folding every round's prompt cost, including any tool definitions/results
        sent along the way, into this one turn, the same way the system prompt's cost is
        folded into the first turn (see docs/specs/session-and-turns.md) —
        `processing_state` becomes `"complete"`, and `last_error` is cleared. On failure,
        `user_message` is mutated to `processing_state="error"` with `last_error` set (the
        failing round's own placeholder(s) are already marked the same way by
        `_send_and_receive`), and the exception is re-raised.

        If `callbacks.cancel_event` is given and the provider raises `ResponseAborted` (because
        it was set mid-stream), `user_message` and every message already appended for this
        turn — any earlier round's completed `tool_use`/`tool_response` messages (their tool
        calls already ran, with real side effects, and stay in history exactly like a
        completed turn's would), plus the aborted round's placeholder(s), already finalized
        with `processing_state="aborted"` by `_send_and_receive` — are left in
        `self._messages` rather than erased. `user_message.processing_state` becomes
        `"aborted"` too, and its `num_tokens` reflects the last *completed* round's prompt
        tokens (`0` if the very first round was the one aborted, since no round completed).
        The exception is re-raised so the caller can report the interruption.
        """
        callbacks = callbacks or TurnEventHandlers()
        self._ensure_system_message()
        self._ensure_tool_defs_message()
        self._ensure_context_files_message()
        self._tool_calls_this_turn = 0
        model_name = self.active_model_name()
        system_prompt = self._resolve_system_prompt()
        reasoning = self._reasoning_params()
        tools = self._tool_registry.tool_definitions() if self._tool_registry is not None else None
        last_completed_result: ProviderResponse | None = None

        try:
            reply, result = self._send_and_receive(
                list(self._messages), system_prompt, model_name, reasoning, tools, callbacks)
            last_completed_result = result

            rounds = 0
            while reply.role == "tool_use":
                rounds += 1
                if rounds > MAX_TOOL_CALL_ROUNDS:
                    logger.warning(
                        "Tool-call round limit reached (%d/%d) for %s; aborting turn.",
                        rounds - 1, MAX_TOOL_CALL_ROUNDS, model_name)
                    raise ToolCallLimitExceeded(
                        f"Exceeded {MAX_TOOL_CALL_ROUNDS} tool-call round trips in one turn.")
                logger.info("Turn tool-call round %d/%d for %s", rounds, MAX_TOOL_CALL_ROUNDS, model_name)
                self._run_tool_calls(reply, callbacks)
                reply, result = self._send_and_receive(
                    list(self._messages), system_prompt, model_name, reasoning, tools, callbacks)
                last_completed_result = result
        except ResponseAborted:
            user_message.processing_state = "aborted"
            if last_completed_result is not None:
                user_message.num_tokens = last_completed_result.prompt_tokens - tokens_before
            logger.info("Turn aborted for %s", model_name)
            raise
        except Exception as exc:
            user_message.processing_state = "error"
            user_message.last_error = str(exc)
            logger.error("Turn failed for %s: %s", model_name, exc)
            raise

        user_message.num_tokens = result.prompt_tokens - tokens_before
        user_message.processing_state = "complete"
        user_message.last_error = None
        logger.debug(
            "Turn complete for %s: user num_tokens=%d, assistant num_tokens=%d, finish_reason=%s",
            model_name, user_message.num_tokens, reply.num_tokens, reply.finish_reason,
        )
        return reply.content

    def send_turn(
        self,
        prompt: str,
        callbacks: TurnEventHandlers | None = None,
    ) -> str:
        """Send one turn of the conversation to the active model and return its response.

        Appends a new user `Message` to the session's history, sends the full history (plus
        the session's resolved system prompt — see `_resolve_system_prompt`) to the
        provider, and mutates that
        same `Message` in place to reflect the outcome (see `_dispatch_turn`). `callbacks`
        (a `TurnEventHandlers`, defaulting to one with every field `None` if omitted) bundles
        every optional hook for this turn: `on_chunk`/`on_thinking_chunk` are invoked with each
        text/reasoning delta as the response streams in; if `cancel_event` is set while the
        response is streaming in, the turn is aborted: `ResponseAborted` is raised, and the
        user `Message` appended here — along with whatever partial reply/thinking content and
        completed tool-call rounds accumulated before the interruption — stays in history with
        `processing_state="aborted"` rather than being removed (see `_dispatch_turn`);
        `on_tool_call_limit_reached` is invoked (with a human-readable prompt) when a tool-call
        safety limit is reached, to ask whether to double it and keep going — see
        `_confirm_limit_increase`; `on_permission_ask` is invoked when a tool call hits an
        `"ask"` permission verdict, to ask whether (and how broadly) to allow it — see
        `_run_tool_calls`.
        """
        tokens_before = self._tokens_recorded_so_far()
        user_message = Message(
            content=prompt,
            role="user",
            num_tokens=0,
            processing_state="pending",
            timestamp=datetime.now(),
        )
        self._messages.append(user_message)
        logger.info(
            "Sending turn to %s (%d messages in context)", self.active_model_name(), len(self._messages))
        return self._dispatch_turn(user_message, tokens_before, callbacks)

    def retry_last_turn(
        self,
        callbacks: TurnEventHandlers | None = None,
    ) -> str:
        """Resend the most recently errored user turn's content, mutating that same `Message`.

        Scans from the end of the history for the last user-role `Message`; if none exists
        or it isn't in `"error"` state, raises `ValueError`. Any messages after it (e.g. a
        partial assistant placeholder left behind by a failed stream) are discarded before
        resending, since they belong to the failed attempt, not the conversation. `callbacks`
        is the same `TurnEventHandlers` bundle `send_turn()` takes, including
        `callbacks.cancel_event` — a retried turn can be aborted mid-stream exactly like an
        original one.
        """
        index = len(self._messages) - 1
        while index >= 0 and self._messages[index].role != "user":
            index -= 1
        if index < 0 or self._messages[index].processing_state != "error":
            raise ValueError("No errored turn to retry.")

        del self._messages[index + 1:]
        errored_message = self._messages[index]
        tokens_before = self._tokens_recorded_so_far() - errored_message.num_tokens
        errored_message.processing_state = "pending"
        logger.info("Retrying errored turn for %s", self.active_model_name())
        return self._dispatch_turn(errored_message, tokens_before, callbacks)

    def run_one_shot(
        self,
        prompt: str,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Run a single, non-interactive turn and return the model's text response.

        Passes no `on_permission_ask`/`on_tool_call_limit_reached` callbacks — there's no
        interactive surface to ask through here — so any `"ask"` permission verdict resolves
        per `config.permission_framework` (see `SessionConfig.permission_framework`), and
        hitting a tool-call limit always raises `ToolCallLimitExceeded`. `send_turn()` already
        loops through every tool-call round on its own until the model returns a final
        plain-text reply, so this call runs the whole task to completion, not just one
        model round trip.
        """
        return self.send_turn(
            prompt, TurnEventHandlers(on_chunk=on_chunk, on_thinking_chunk=on_thinking_chunk))
