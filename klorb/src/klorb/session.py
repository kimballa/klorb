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
from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME, DirRules
from klorb.permissions.file_access import FileRules
from klorb.permissions.table import (
    MultiPermissionAskRequired,
    PermissionAskItem,
    PermissionAskRequired,
    PermissionOverride,
)
from klorb.role import COORDINATOR_ROLE_NAME, Role, get_role
from klorb.system_prompt import SystemPrompt
from klorb.token_estimate import estimate_tokens
from klorb.tool_call_log import log_tool_call, tool_call_logging_enabled
from klorb.tools.ask.common import AskUserQuestionsRequired, QuestionOption
from klorb.tools.scratchpad.common import Scratchpad
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

PERMISSION_FRAMEWORK_INTERJECTIONS: dict[PermissionFramework, str] = {
    "auto": (
        "The user has changed your permission framework to 'automatic'. Any tool call you "
        "issue will be approved, so you must only generate tool calls that will be safe "
        "without human review."
    ),
    "ask": (
        "The user has changed your permission framework to 'ask' mode. You can propose any "
        "tool calls and the human will review them for approval if necessary."
    ),
    "deny": (
        "The user has changed your permission framework to 'deny approval requests'. Any "
        "tool call you issue that is not already allow-listed will be denied."
    ),
}
"""Text `Session.set_permission_framework()` queues for `send_turn()` to wrap in a
`<SystemInterjection subject="PermissionFramework">` tag and prepend to the next user turn's
prompt, keyed by the newly-set `PermissionFramework` value — see
`Session._pending_permission_framework_interjection` and docs/specs/permissions.md's
"Permission framework change interjection" section."""

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


def _wrap_system_interjection(subject: str, message: str) -> str:
    """Wrap `message` in a `<SystemInterjection subject="{subject}">...</SystemInterjection>`
    tag pair, so a model can tell an out-of-band harness notice apart from the user's own
    turn content within the same `Message.content` — see `Session.send_turn()`'s handling of
    `_pending_permission_framework_interjection`."""
    return f'<SystemInterjection subject="{subject}">\n{message}\n</SystemInterjection>'



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
    """Passed to `on_permission_ask` once per item needing a decision — either from a plain
    `PermissionAskRequired` (always exactly one item) or a `MultiPermissionAskRequired` (asked
    about one at a time, in order — see `Session._run_tool_calls`): the resolved candidate
    resource and a human-readable description of the access, for a UI to build its prompt from
    without re-parsing the raised exception's message string.

    Exactly one of `path`/`command` is set, or neither, mirroring
    `klorb.permissions.table.PermissionAskItem`: `path` (plus `is_write`) for a directory-access
    item; `command` (an argv pattern) for a bash-command-rule item with no filesystem resource;
    neither for a structural item with no persistable rule at all (only `"once"`/deny make sense
    for that last case — see `PermissionDecision`). `command_text`, when set (any `BashTool`-
    originated item, regardless of which of `path`/`command`/neither it also carries), is the
    full raw command string the item's own `resource_description` detail belongs to — see
    `PermissionAskItem.command_text`. `is_compound` mirrors `PermissionAskItem.is_compound`
    alongside it: `True` when `command_text` is a compound command (more than one simple
    command) that this one item is only a part of. `item_command_text` mirrors
    `PermissionAskItem.item_command_text`: the exact source text of just the one statement this
    item is actually about, distinct from `command_text`'s whole raw command — a UI should
    prefer showing this as the prominent per-item preview (see `PermissionAskItem.
    item_command_text` for why `command_text` alone isn't enough for a compound command).

    `sibling_items`, set by `Session._resolve_multi_permission_ask` to the full
    `MultiPermissionAskRequired.items` list (including the item this context is itself about, in
    the same order), lets a UI batch work across a whole compound command's several asks even
    though they're each still asked about one at a time, in series — e.g.
    `klorb.tui.repl.ReplApp._confirm_permission_ask` uses it to send `klorb.permissions.
    risk_classifier.classify_command_risk()` every item in one request the first time any of
    them is seen, rather than once per item. `None` for a plain single-item
    `PermissionAskRequired` ask (that path never has `command_text` set at all, so there is
    nothing for a command-risk classifier to batch there in the first place). `Session` itself
    never reads this field back — it only threads data it already has for whichever callback
    consumes it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    path: Path | None = None
    is_write: bool = False
    command: list[str] | None = None
    command_text: str | None = None
    is_compound: bool = False
    item_command_text: str | None = None
    resource_description: str
    sibling_items: list[PermissionAskItem] | None = None


class PermissionDecision(BaseModel):
    """The user's answer to one `PermissionAskContext` prompt, returned by `on_permission_ask`.

    `action` and `scope` are independent axes: `"allow"`/`"deny"` cross with `"once"` (retries
    without persisting anything, via a one-shot `ToolSetupContext.permission_override` covering
    whichever of `path`/`command`/`resource_description` the item carries — see
    `klorb.permissions.table.PermissionOverride`) and `"session"`/`"workspace"`/`"homedir"`
    (persistent for a `path` or `command` item — `Session._retry_after_permission_decision`/
    `_retry_after_multi_permission_decisions` apply the grant itself, via `klorb.permissions.
    grant.apply_permission_grant`/`klorb.permissions.command_grant.apply_command_permission_grant`,
    before retrying; a structural item has no rule to persist at any scope other than `"once"`,
    since it identifies no filesystem path or command pattern — `on_permission_ask` only needs
    to ask the user and report their choice back, never apply or persist anything itself).
    `other_text`, if set, means the user typed free-text instead of picking a grid cell —
    equivalent to `action="deny", scope="once"` but with the explanation surfaced to the model
    alongside the denial, so it can act on the redirection without a second round trip."""

    action: Literal["allow", "deny"]
    scope: Literal["once", "session", "workspace", "homedir"] = "once"
    other_text: str | None = None


class AskUserQuestionsItemContext(BaseModel):
    """Passed to `on_ask_user_questions` once per question in an `AskUserQuestionsRequired`
    batch, asked about one at a time, in order (see `Session._resolve_ask_user_questions`) —
    mirroring how a `MultiPermissionAskRequired`'s items are asked about one at a time via
    `on_permission_ask`. `index`/`total` (e.g. `1`/`3`) let a UI render "Question 2 of 3"
    without re-deriving it from a running count of its own calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    header: str
    question: str
    options: list[QuestionOption]
    index: int
    total: int


class AskUserQuestionsAnswer(BaseModel):
    """The user's answer to one `AskUserQuestionsItemContext` prompt, returned by
    `on_ask_user_questions`. `answer` is the final rendered string for a selected option
    (`"label"`, or `"label: description"` when the option has one — see
    `klorb.tools.ask.common.format_answer`) or the user's raw free-text answer;
    it is `None` only when `cancelled` is set, since there is no deny/allow axis to fall back
    to here the way `PermissionDecision` has — Escape means "stop asking me this", not "deny
    this one item", so `Session._resolve_ask_user_questions` short-circuits the rest of the
    batch instead of recording a per-item denial."""

    answer: str | None = None
    cancelled: bool = False


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
    raw_arguments: str | None = None
    """The model's unparsed `arguments` string, set only when it failed to parse as JSON (so
    `args` is `{}` and `error` describes the parse failure) — lets a consumer show the model's
    actual malformed output instead of just the parse error. `None` in every other case,
    including a syntactically valid-but-wrong-shaped `args`, since `apply()` reports that
    failure itself."""


class TurnEventHandlers(BaseModel):
    """Immutable bundle of the optional callbacks (and cancellation signal) a caller can
    supply for one turn: `on_chunk`/`on_thinking_chunk` (streamed response text),
    `cancel_event` (abort a turn mid-stream), `on_tool_call_limit_reached` (ask whether to
    raise a safety cap), `on_permission_ask` (ask how to resolve an `"ask"` permission
    verdict), `on_ask_user_questions` (ask the user one `AskUserQuestions` tool-call
    question), and `on_tool_call` (report one finished tool call, for display). Replaces
    passing these as separate keyword arguments through `send_turn()`/`retry_last_turn()`/
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
    on_ask_user_questions: (
        Callable[[AskUserQuestionsItemContext], AskUserQuestionsAnswer] | None
    ) = None
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
        scratchpad_path: str | None = None,
    ) -> None:
        self.config = config
        self.id = session_id or generate_session_id()
        self._role = get_role(config.role_name)
        self._provider = provider or OpenRouterApiProvider()
        self._model_registry = model_registry or ModelRegistry()
        self._system_prompt = SystemPrompt(config, self._role, self._model_registry)
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
        if tool_registry is not None:
            tool_registry.session = self
        self.tool_state: dict[str, Any] = {}
        """Per-session runtime state a `Tool` implementation wants to keep across calls within
        this one session (e.g. `BashTool`'s one-time sandbox-fallback notice), keyed by tool
        name (`tool_state["Bash"]`). Distinct from `config` (`SessionConfig`, user-configurable
        settings only): this is ad hoc, tool-private bookkeeping, never read or written by
        `Session` itself, and never persisted to disk. A `Tool` accesses it via
        `self.context.session.tool_state` (see `klorb.tools.setup_context.ToolSetupContext.
        session`) and must `.setdefault(...)` its own key rather than assuming it's pre-populated
        — this dict starts empty for every new `Session`."""
        self._tool_calls_this_session = 0
        self._tool_calls_this_turn = 0
        self._compatibility_claude_markdown = (
            process_config.compatibility_claude_markdown if process_config is not None else False
        )
        self._log_tool_calls = tool_call_logging_enabled(
            process_config.log_tool_calls if process_config is not None else None
        )
        """Whether to append every finished tool call to `tool-calls.log` via
        `klorb.tool_call_log.log_tool_call` — resolved once, at construction time, from
        `process_config.log_tool_calls` (itself set by the `tools.logCalls` config key or
        the `--log-tool-calls`/`--no-log-tool-calls` CLI flag pair) combined with the
        `LOG_TOOL_CALLS` environment variable via `tool_call_logging_enabled()`: an
        explicit `True`/`False` from config or CLI is authoritative, and the environment
        variable is only consulted when `process_config.log_tool_calls` is `None` (e.g.
        a caller that constructs a `Session` without a `ProcessConfig` at all, as most
        unit tests do)."""
        self._messages: list[Message] = []
        self._last_known_real_tokens = 0
        """The most recent `prompt_tokens + completion_tokens` reported for any completed
        round (`_send_and_receive`'s success path) -- always the exact current size of the
        whole conversation, since a round's `prompt_tokens` already reflects every message
        sent as input to it, however many earlier rounds contributed to that history. This is
        what `total_tokens_used()` reports as the settled portion of its total, instead of
        summing every message's own `num_tokens`, which would double-count an earlier round's
        reply: its completion tokens are billed once as that round's own output, then billed
        again as prompt tokens once resent as history to the next round. See
        docs/adrs/null-estimated-tokens-when-a-real-count-supersedes-them.md."""
        self._context_files_seeded = False
        """Whether `send_turn()` has already computed (and, if non-`None`, prepended) the
        one-shot `ProjectGuidance` `<SystemInterjection>` carrying the workspace's
        context-instruction files (`.klorb/INSTRUCTIONS.md`, `AGENTS.md`, and `CLAUDE.md` when
        compatibility is enabled) onto the very first turn's prompt — see
        `_build_context_files_interjection()`. Set unconditionally the first time it's
        checked, even when there was nothing to prepend (workspace untrusted, or no applicable
        file exists on disk), so a later turn never re-reads the filesystem or re-prepends
        anything."""
        self._pending_permission_framework_interjection: str | None = None
        """Set by `set_permission_framework()` to the harness message queued for the next
        `send_turn()` call to prepend onto its `prompt`, or `None` if no permission-framework
        change is pending. Overwritten (not accumulated) on every call to
        `set_permission_framework()`, so several changes before the next turn collapse to a
        single interjection reflecting only the final mode — see docs/specs/permissions.md's
        "Permission framework change interjection" section."""
        self._standing_interjection_providers: dict[str, Callable[[], str | None]] = {}
        """Providers registered via `register_standing_interjection()`, keyed by subject.
        Distinct from `_pending_permission_framework_interjection`: that field is one-shot and
        edge-triggered (set once, prepended once, cleared); every provider here is polled on
        every `send_turn()` call for as long as it stays registered, for level-triggered,
        standing conditions (e.g. `BashTool`'s live persistent shell) — see
        `register_standing_interjection` and docs/adrs/standing-interjections-complement-one-
        shot-for-level-triggered-state.md."""
        self._teardown_callbacks: dict[str, Callable[[], None]] = {}
        """Callbacks registered via `register_teardown()`, keyed by subject, invoked once each
        by `close()`. See `register_teardown`/`close`."""
        self.scratchpad = Scratchpad(scratchpad_path)
        """This session's scratchpad file (see `klorb.tools.scratchpad.common.Scratchpad`,
        which does all the work of resolving `scratchpad_path` — a caller-supplied path to
        reuse, or a freshly provisioned one — and of cleaning it up again below). A `Tool`
        reads it via `self.context.session.scratchpad.path` (see `klorb.tools.scratchpad.common.
        scratchpad_path`), the same access pattern as `tool_state` above."""
        self.register_teardown("Scratchpad", self.scratchpad.cleanup)

    @property
    def role(self) -> Role:
        """Return the `Role` this session's agent operates as, built (in `__init__`, from
        `config.role_name`) via `klorb.role.get_role()`."""
        return self._role

    @property
    def system_prompt(self) -> SystemPrompt:
        """Return the `SystemPrompt` that resolves this session's system prompt, built in
        `__init__` from the live `config`, `Role`, and `ModelRegistry`. Re-resolves fresh on
        each call (see `SystemPrompt.resolve`), so a mid-session `config.model` change is
        reflected on the next turn."""
        return self._system_prompt

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

    def load_messages(self, messages: list[Message]) -> None:
        """Replace this session's conversation history with `messages` — e.g. restoring a
        previous interactive session's history from `klorb.workspace.last_session`. Intended
        to be called once, immediately after construction, before any `send_turn()`: a
        `role="system"`/`role="tool_defs"` bookkeeping message already present in `messages`
        is left as-is rather than duplicated, since `_ensure_system_message()`/
        `_ensure_tool_defs_message()` each skip inserting one if one is already there — see
        their docstrings for why the possibly-stale content of either doesn't matter, since
        the live system prompt and tool definitions are always resolved fresh and sent
        out-of-band on every turn regardless of what's recorded here.
        """
        self._messages = list(messages)

    def set_permission_framework(self, value: PermissionFramework) -> None:
        """Change `config.permission_framework` to `value` and queue a system-harness
        interjection (`PERMISSION_FRAMEWORK_INTERJECTIONS[value]`) for `send_turn()` to
        prepend onto the prompt of the next turn it dispatches. Callers that let the user
        change this mode mid-conversation (e.g. `klorb.tui.repl.ReplApp`'s Shift+Tab
        cycling) should call this instead of assigning `config.permission_framework`
        directly, so the model is told about the change. The enforcement value itself takes
        effect immediately, exactly as a direct assignment would — only the model-facing
        notice is deferred to the next turn. Calling this again before the next turn starts
        overwrites the pending interjection rather than queuing a second one, so only the
        final mode setting controls what's sent — see docs/specs/permissions.md's
        "Permission framework change interjection" section.

        Raises `ValueError` if `value` isn't one of `PERMISSION_FRAMEWORK_INTERJECTIONS`'
        keys (every `PermissionFramework` literal) — `SessionConfig` doesn't validate
        assignment, so a caller passing an untyped or externally-sourced string (rather than
        a type-checked `PermissionFramework` literal) would otherwise leave `config` and the
        pending interjection out of sync with each other, or raise an opaque `KeyError`
        rather than a caller-actionable message.
        """
        if value not in PERMISSION_FRAMEWORK_INTERJECTIONS:
            raise ValueError(
                f"permission_framework must be one of "
                f"{sorted(PERMISSION_FRAMEWORK_INTERJECTIONS)}, got {value!r}")
        self.config.permission_framework = value
        self._pending_permission_framework_interjection = PERMISSION_FRAMEWORK_INTERJECTIONS[value]

    def register_standing_interjection(self, subject: str, provider: Callable[[], str | None]) -> None:
        """Register `provider` to be polled by every future `send_turn()` call: whenever it
        returns a message (rather than `None`), that message is wrapped in a
        `<SystemInterjection subject="{subject}">` tag (see `_wrap_system_interjection`) and
        prepended onto the next turn's `prompt` — every turn the condition still holds, not just
        once. This is the standing, level-triggered counterpart to
        `_pending_permission_framework_interjection`'s one-shot, edge-triggered field: use this
        for a condition that can keep being true across many turns (e.g. `BashTool`'s live
        session-scoped persistent shell), not a single change event.

        Re-registering under the same `subject` overwrites the previous provider rather than
        accumulating a second one, so a caller that constructs a fresh instance per call (e.g.
        `ToolRegistry.instantiate_tool()`, per its own docstring) can safely (re)register on
        every call without leaking entries. `provider` itself is responsible for deciding whether
        its condition still holds — returning `None` once it no longer applies is what stops the
        interjection from appearing; there is no separate unregister call. See
        docs/adrs/standing-interjections-complement-one-shot-for-level-triggered-state.md.
        """
        self._standing_interjection_providers[subject] = provider

    def register_teardown(self, subject: str, teardown: Callable[[], None]) -> None:
        """Register `teardown` to be invoked once by `close()`, keyed by `subject`.
        Re-registering under the same `subject` overwrites the previous callback rather than
        accumulating a second one — the same idempotency contract as
        `register_standing_interjection`, for the same reason (a caller that constructs a fresh
        instance per call can safely re-register every time)."""
        self._teardown_callbacks[subject] = teardown

    def close(self) -> None:
        """Tear down any live per-session resources registered via `register_teardown` --
        `BashTool`'s persistent shell, if one is alive, and `self.scratchpad`'s cleanup (see
        `klorb.tools.scratchpad.common.Scratchpad.cleanup`, registered in `__init__`). Idempotent:
        calling this when nothing is registered, or calling it twice, is a no-op the second time
        onward since each teardown callback (e.g. `PersistentShell.kill`, `Scratchpad.cleanup`)
        is itself safe to call more than once.

        Callers that replace a `Session` outright (`klorb.tui.repl.ReplApp.clear_session()`) must
        call this on the outgoing `Session` first — nothing else tears it down, and a live
        `subprocess.Popen` handle would otherwise leak for the rest of the klorb process's
        lifetime. See docs/plans/archive/005-session-scoped-bash-terminals.md.
        """
        for teardown in self._teardown_callbacks.values():
            teardown()
        self._teardown_callbacks.clear()

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

    def _settle_estimated_tokens(self) -> None:
        """Clear `estimated_tokens` on every message in the buffer: called right after
        `_last_known_real_tokens` is updated from a just-completed round's real
        `prompt_tokens`/`completion_tokens` (see `_send_and_receive`), since that round's
        `prompt_tokens` already reflects every message currently in history as this round's
        input -- there's nothing left for any message's own estimate to cover. Only ever
        called from that success path, so a message can't be `"aborted"` here: aborting
        raises out of `_send_and_receive` before this point, leaving that round's
        placeholder(s) with their estimate intact instead (see
        docs/adrs/null-estimated-tokens-when-a-real-count-supersedes-them.md).
        """
        for message in self._messages:
            message.estimated_tokens = None

    def total_tokens_used(self) -> int:
        """Return the running total of tokens consumed by the conversation so far:
        `_last_known_real_tokens` (the last completed round's real, server-reported
        `prompt_tokens + completion_tokens` -- already the exact size of the whole
        conversation as of that round, see `_last_known_real_tokens`), plus `estimated_tokens`
        for any message not yet covered by that report -- content streaming in for the round
        currently in flight, or (before the first round of the session completes) the initial
        system/tool_defs/user content. This gives the context-usage footer a live number that
        updates as content streams in, without double-counting an earlier round's reply once
        it's resent as history to a later one."""
        return self._last_known_real_tokens + sum(
            message.estimated_tokens or 0 for message in self._messages)

    def max_context_window(self) -> int | None:
        """Return the active model's max context window in tokens, or `None` if the
        active model isn't registered or doesn't report one.
        """
        model = self._active_model()
        if model is None:
            return None
        return model.capabilities().get("max_context_window")

    def _resolve_system_prompt(self) -> str:
        """Resolve the system prompt for the active turn by delegating to the session's
        `SystemPrompt` (see `klorb.system_prompt.SystemPrompt.resolve`), which runs the
        default and role walks and concatenates their results. Re-derived fresh each place
        it's needed, so it always reflects the *current* active model — see
        docs/specs/roles-and-system-prompts.md."""
        return self._system_prompt.resolve()

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
            estimated_tokens=estimate_tokens(system_prompt),
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
        definitions_json = json.dumps(definitions)
        insert_index = 1 if self._messages and self._messages[0].role == "system" else 0
        self._messages.insert(insert_index, Message(
            content=definitions_json,
            role="tool_defs",
            num_tokens=0,
            estimated_tokens=estimate_tokens(definitions_json),
            processing_state="complete",
            timestamp=datetime.now(),
        ))

    def _build_context_files_interjection(self) -> str | None:
        """Return the body `send_turn()` wraps in a `<SystemInterjection subject=
        "ProjectGuidance">` tag and prepends onto the very first turn's prompt, or `None` if
        there's nothing to say.

        Returns `None` outright, without touching the filesystem at all, whenever
        `config.workspace.trusted` is `False`: `.klorb/INSTRUCTIONS.md`, `AGENTS.md`, and
        `CLAUDE.md` are project-supplied content, and a hostile, downloaded-and-unzipped
        repository could ship any of them to smuggle instructions into the model's context the
        moment the user runs klorb from inside it — the same risk `.klorb/klorb-config.json`'s
        own trust gate exists to close (see docs/specs/projects-and-trust.md). So none of them
        are ever read into the prompt until the user has explicitly trusted the workspace,
        exactly like that config layer.

        Otherwise reads each of `_applicable_context_filenames()` (in priority order) that
        exists on disk, relative to `config.workspace.path`, and wraps each one's contents in
        a `<ContextFile filename="..." priority="N">` tag, `N` starting at `1` in that same
        priority order — giving the model an explicit signal for which file should win if two
        ever conflict."""
        if not self.config.workspace.trusted:
            return None

        context_files: list[tuple[str, str]] = []
        for filename in self._applicable_context_filenames():
            path = self.config.workspace.path / filename
            if path.is_file():
                context_files.append((filename, path.read_text()))
        if not context_files:
            return None

        sections = [
            f'<ContextFile filename="{filename}" priority="{index + 1}">\n{contents}\n'
            f'</ContextFile>'
            for index, (filename, contents) in enumerate(context_files)
        ]
        return (
            "This workspace contains one or more files with instructions and context for "
            "working in this repository. Treat them as standing guidance about the "
            "project's conventions and requirements, not a task to act on directly.\n\n" +
            "\n\n".join(sections)
        )

    def _applicable_context_filenames(self) -> list[str]:
        """Return the ordered list of context-instruction filenames to read, relative to the
        workspace root, most authoritative first: `.klorb/INSTRUCTIONS.md` (priority 1 —
        durable per-project instructions kept alongside `klorb-config.json` rather than at the
        workspace root), then `AGENTS.md` (priority 2 — klorb's own root-level convention),
        then `CLAUDE.md` (priority 3) when `_compatibility_claude_markdown` is enabled."""
        filenames = [f"{KLORB_PROJECT_DIR_NAME}/INSTRUCTIONS.md", "AGENTS.md"]
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
        content): for a persistent `scope`, first applies the grant `decision` implies via
        `klorb.permissions.grant.apply_permission_grant` — passing `self.config` and
        `self._process_config` (possibly `None`; that function skips the process-wide ripple,
        but still persists to disk, in that case) — then retries the call exactly once, same as
        `scope="once"` (which instead retries via a one-shot `ToolSetupContext.
        permission_override`, persisting nothing — only meaningful for `action="allow"`; a
        `"once"` deny needs no override at all). Reports a denial for `action="deny"` (any
        scope) or `other_text` with no retry. Any exception from the retried call (including a
        second `PermissionAskRequired`) is reported the same generic way an ordinary tool
        failure is — never a second ask.
        """
        assert ask_exc.path is not None
        if decision.other_text is not None:
            return None, f"Permission denied: {ask_exc}. User note: {decision.other_text}"
        if decision.action == "deny":
            if decision.scope != "once":
                # Local import: `klorb.permissions.grant` imports `SessionConfig` from this
                # module, so importing it at module scope here would be circular.
                from klorb.permissions.grant import apply_permission_grant
                apply_permission_grant(
                    "deny", decision.scope, self.config, self._process_config,
                    ask_exc.path, ask_exc.is_write)
            return None, f"Permission denied: {ask_exc}"
        if decision.scope in ("session", "workspace", "homedir"):
            from klorb.permissions.grant import apply_permission_grant
            apply_permission_grant(
                "allow", decision.scope, self.config, self._process_config,
                ask_exc.path, ask_exc.is_write)
        assert self._tool_registry is not None
        try:
            if decision.scope == "once":
                override = PermissionOverride(paths=frozenset({ask_exc.path}))
                tool = self._tool_registry.instantiate_tool(call.name, permission_override=override)
            else:
                tool = self._tool_registry.instantiate_tool(call.name)
            return tool.apply(args), None
        except Exception as exc:
            logger.warning("Retried tool call %s(%s) failed: %s", call.name, call.arguments, exc)
            return None, str(exc)

    def _retry_after_multi_permission_decisions(
        self,
        call: ToolCallRequest,
        args: dict[str, Any],
        items: list[PermissionAskItem],
        decisions: list[PermissionDecision],
    ) -> tuple[Any, str | None]:
        """Resolve a `MultiPermissionAskRequired`'s items into a `(result, error)` pair, given
        one `PermissionDecision` already collected per item (`_run_tool_calls` stops collecting
        — and calls this with a shorter `decisions` list than `items` — at the first `"deny"`
        answer; see that method). Applies every persistent-scope `"allow"` decision's grant
        (via `klorb.permissions.grant.apply_permission_grant` for a `path` item,
        `klorb.permissions.command_grant.apply_command_permission_grant` for a `command` item —
        a structural item with neither has no rule to persist, so any `decision` for it other
        than `action="deny"` is a no-op grant-wise). Every `scope="once"` decision instead
        contributes its item to a single `PermissionOverride` (see `ToolSetupContext.
        permission_override`) covering all of them at once, since retrying happens exactly once
        for the whole call — never once per item, as re-parsing/re-evaluating after each
        individual grant would be wasted work. If `decisions` is shorter than `items` (an item
        was denied), or any collected decision is itself a denial, reports that denial with no
        retry at all.
        """
        for item, decision in zip(items, decisions):
            if decision.action == "deny" or decision.other_text is not None:
                reason = f"Permission denied: {item.resource_description}"
                if decision.other_text is not None:
                    reason += f". User note: {decision.other_text}"
                return None, reason

        if len(decisions) < len(items):
            denied_item = items[len(decisions)]
            return None, f"Permission denied: {denied_item.resource_description}"

        # Local imports: both modules import `SessionConfig` from this module, so importing them
        # at module scope here would be circular.
        from klorb.permissions.command_grant import apply_command_permission_grant
        from klorb.permissions.grant import apply_permission_grant

        once_paths: set[Path] = set()
        once_commands: set[tuple[str, ...]] = set()
        once_reasons: set[str] = set()
        for item, decision in zip(items, decisions):
            if decision.scope == "once":
                if item.path is not None:
                    once_paths.add(item.path)
                elif item.command is not None:
                    once_commands.add(tuple(item.command))
                else:
                    once_reasons.add(item.resource_description)
                continue
            if item.path is not None:
                apply_permission_grant(
                    decision.action, decision.scope, self.config, self._process_config,
                    item.path, item.is_write)
            elif item.command is not None:
                apply_command_permission_grant(
                    decision.action, decision.scope, self.config, self._process_config, item.command)

        override = None
        if once_paths or once_commands or once_reasons:
            override = PermissionOverride(
                paths=frozenset(once_paths), commands=frozenset(once_commands),
                reasons=frozenset(once_reasons))

        assert self._tool_registry is not None
        try:
            tool = self._tool_registry.instantiate_tool(call.name, permission_override=override)
            return tool.apply(args), None
        except Exception as exc:
            logger.warning("Retried tool call %s(%s) failed: %s", call.name, call.arguments, exc)
            return None, str(exc)

    def _resolve_multi_permission_ask(
        self,
        call: ToolCallRequest,
        args: dict[str, Any],
        multi_ask_exc: MultiPermissionAskRequired,
        callbacks: TurnEventHandlers,
    ) -> tuple[Any, str | None]:
        """Resolve a `MultiPermissionAskRequired` into a `(result, error)` pair, branching on
        `config.permission_framework` exactly like the single-item `PermissionAskRequired` path
        does — except an item with no `path` is never automatically failed closed here (see
        `MultiPermissionAskRequired`'s own docstring for why). `"ask"` asks about every item in
        order via `callbacks.on_permission_ask`, stopping at the first `action="deny"` (or
        `other_text`-bearing) answer — the remaining items are never asked about, and
        `_retry_after_multi_permission_decisions` reports that denial without retrying.
        """
        if self.config.permission_framework == "deny":
            logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, multi_ask_exc)
            return None, str(multi_ask_exc)
        if self.config.permission_framework == "auto":
            logger.info(
                "Auto-approving permission ask under permissionFramework=auto: %s", multi_ask_exc)
            auto_decisions = [
                PermissionDecision(action="allow", scope="session") for _ in multi_ask_exc.items]
            return self._retry_after_multi_permission_decisions(
                call, args, multi_ask_exc.items, auto_decisions)
        if callbacks.on_permission_ask is None:
            logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, multi_ask_exc)
            return None, str(multi_ask_exc)

        decisions: list[PermissionDecision] = []
        for item in multi_ask_exc.items:
            decision = callbacks.on_permission_ask(PermissionAskContext(
                path=item.path, is_write=item.is_write, command=item.command,
                command_text=item.command_text, is_compound=item.is_compound,
                item_command_text=item.item_command_text,
                resource_description=item.resource_description,
                sibling_items=multi_ask_exc.items))
            decisions.append(decision)
            if decision.action == "deny" or decision.other_text is not None:
                break
        return self._retry_after_multi_permission_decisions(call, args, multi_ask_exc.items, decisions)

    def _resolve_ask_user_questions(
        self,
        call: ToolCallRequest,
        ask_exc: AskUserQuestionsRequired,
        callbacks: TurnEventHandlers,
    ) -> tuple[Any, str | None]:
        """Resolve an `AskUserQuestionsRequired` into a `(result, error)` pair: asks
        `callbacks.on_ask_user_questions` about each of `ask_exc.questions` in turn, building
        `result["answers"]` from the collected `AskUserQuestionsAnswer`s. Unlike a permission
        ask, there is no `config.permission_framework` branching (asking the user isn't a
        resource-access verdict an "auto"/"deny" framework applies to — see
        `docs/adrs/ask-user-questions-tool-bypasses-permission-tables.md`) and no "retry
        `tool.apply()`" step afterward: asking the question was this tool's entire job, so the
        collected answers directly become the result.

        With no callback given (e.g. a headless one-shot run — see `_send_and_receive`'s
        prompt-path callers), fails closed the same as an unhandled `PermissionAskRequired`
        would, since there is nobody to ask. A `cancelled` answer for any question
        short-circuits the rest of the batch: the call fails, naming the cancelled question and
        echoing back any answers already collected for earlier questions in the same call, so
        that information isn't silently lost.
        """
        if callbacks.on_ask_user_questions is None:
            logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, ask_exc)
            return None, str(ask_exc)

        total = len(ask_exc.questions)
        answers: list[dict[str, Any]] = []
        for index, question in enumerate(ask_exc.questions):
            answer = callbacks.on_ask_user_questions(AskUserQuestionsItemContext(
                header=question.header, question=question.question, options=question.options,
                index=index, total=total))
            if answer.cancelled:
                collected = ", ".join(a["header"] for a in answers) or "none"
                return None, (
                    f"User declined to answer question {index + 1}/{total} "
                    f"('{question.header}': {question.question}). Answers already collected "
                    f"for earlier questions in this call: {collected}. Don't keep guessing or "
                    "re-asking the same thing a different way -- reconsider your approach, or "
                    "ask a clearer, narrower question."
                )
            answers.append({
                "header": question.header,
                "question": question.question,
                "answer": answer.answer,
            })
        return {"answers": answers}, None

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
        same result-or-error outcome as raw data, for a UI to render. When `self._log_tool_calls`
        is set, also appends the call's name/arguments and result/error to `tool-calls.log` via
        `klorb.tool_call_log.log_tool_call` — an out-of-band record independent of both
        `callbacks.on_tool_call` and the ordinary `logging` module.

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
        `PermissionDecision(action="allow", scope="session")`, also without invoking any
        callback; `"ask"` asks `callbacks.on_permission_ask`, if given, for a `PermissionDecision`
        and retries or denies accordingly (see `_retry_after_permission_decision`) — with no
        callback (e.g. a headless one-shot run left at the `"ask"` default), it fails closed the
        same as `"deny"`. An exception with no `path` (a call site that didn't supply one)
        always fails closed, regardless of `permission_framework`.

        A call that raises `MultiPermissionAskRequired` instead (today, only `BashTool`, whose
        one parsed command can independently touch several commands/redirection targets) is
        resolved item-by-item, in order, via the same `on_permission_ask` callback and the same
        `permission_framework` branching — see `_resolve_multi_permission_ask`. Unlike a plain
        `PermissionAskRequired`, an item here with no `path` (a bare command-pattern or
        structural ask) is *not* automatically failed closed: it's asked about (or auto-approved
        under `"auto"`) exactly like any other item, just persisted through `CommandRules`
        grant machinery instead of `readDirs`/`writeDirs` when it has a `command`, or not
        persisted at all when it has neither (see `PermissionAskItem`). The first item answered
        `action="deny"` (or with `other_text` set) short-circuits: no further items are asked
        about, and the whole call is denied.

        A call that raises `AskUserQuestionsRequired` (only `AskUserQuestionsTool`) is resolved
        by `_resolve_ask_user_questions`: each question is asked in turn via
        `callbacks.on_ask_user_questions`, with no `permission_framework` branching (asking the
        user isn't a resource-access verdict) and no callback means it fails closed exactly like
        an unhandled `PermissionAskRequired` would. A `cancelled` answer for any question
        short-circuits the remaining ones and fails the whole call.

        Failing closed (either exception type) reports the error back to the model as this
        call's `tool_response`, exactly like any other tool exception.

        `call.arguments` is parsed as JSON before any of the above: a model-generated
        `arguments` string that fails to parse (`json.JSONDecodeError`) is reported back the
        same way — as this call's `tool_response`, with `args={}` (no tool ever runs) — rather
        than propagating out and aborting the whole turn, so a single malformed tool call
        doesn't take down every other call/round in flight. `ToolCallEvent.raw_arguments`
        carries the unparsed string in this case, for a UI to display.
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
            args: dict[str, Any] = {}
            result: Any = None
            error: str | None = None
            raw_arguments: str | None = None
            try:
                args = json.loads(call.arguments) if call.arguments else {}
            except json.JSONDecodeError as json_exc:
                logger.warning(
                    "Tool call %s had malformed JSON arguments (%s): %s",
                    call.name, json_exc, call.arguments)
                raw_arguments = call.arguments
                error = f"Invalid JSON in tool call arguments: {json_exc}"

            if error is None:
                try:
                    tool = self._tool_registry.instantiate_tool(call.name)
                    logger.debug("Tool call %s parsed arguments: %r", call.name, args)
                    result = tool.apply(args)
                    logger.info("Tool call %s succeeded: %s", call.name, result)
                except MultiPermissionAskRequired as multi_ask_exc:
                    result, error = self._resolve_multi_permission_ask(call, args, multi_ask_exc, callbacks)
                except AskUserQuestionsRequired as ask_questions_exc:
                    result, error = self._resolve_ask_user_questions(call, ask_questions_exc, callbacks)
                except PermissionAskRequired as ask_exc:
                    if ask_exc.path is None or self.config.permission_framework == "deny":
                        logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, ask_exc)
                        error = str(ask_exc)
                    elif self.config.permission_framework == "auto":
                        logger.info(
                            "Auto-approving permission ask under permissionFramework=auto: %s", ask_exc)
                        decision = PermissionDecision(action="allow", scope="session")
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
            if self._log_tool_calls:
                log_tool_call(call.name, args, result, error)
            if callbacks.on_tool_call is not None:
                callbacks.on_tool_call(ToolCallEvent(
                    call_id=call.id, name=call.name, args=args, result=result, error=error,
                    raw_arguments=raw_arguments))
            self._messages.append(Message(
                content=content,
                role="tool_response",
                num_tokens=0,
                estimated_tokens=estimate_tokens(content),
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

        Both placeholders' `estimated_tokens` are refreshed from `klorb.token_estimate.
        estimate_tokens` on every chunk while streaming (so `Session.total_tokens_used()` has
        a live number before this round trip completes). On success, every message currently
        in history -- not just these two placeholders -- has `estimated_tokens` cleared back
        to `None` via `_settle_estimated_tokens()`, and `Session._last_known_real_tokens` is
        updated to `result.prompt_tokens + result.message.num_tokens`: that sum is already the
        exact size of the whole conversation as of this round, since `prompt_tokens` reflects
        every message just sent as this round's input, however many earlier rounds
        contributed to it.

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
            placeholder.estimated_tokens = estimate_tokens("".join(placeholder.streaming_content))
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
            thinking_placeholder.estimated_tokens = estimate_tokens(
                "".join(thinking_placeholder.streaming_content))
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

        self._last_known_real_tokens = result.prompt_tokens + result.message.num_tokens
        self._settle_estimated_tokens()

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
            logger.error("Turn failed for %s: %s", model_name, exc, exc_info=True)
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

        If a permission-framework change is pending (`set_permission_framework()` was called
        since the last turn), the queued interjection is wrapped in a `<SystemInterjection
        subject="PermissionFramework">` tag (see `_wrap_system_interjection`) and prepended
        onto `prompt` before it's stored as the user `Message`'s content — see
        docs/specs/permissions.md's "Permission framework change interjection" section — and
        the pending state is cleared, so it's applied exactly once. After that, every provider
        registered via `register_standing_interjection()` is polled (in a fixed, deterministic
        order — sorted by subject) and any non-`None` result is prepended the same way, but not
        cleared: a standing interjection keeps appearing on every subsequent turn for as long as
        its provider keeps returning a message.

        Finally, the very first time `send_turn()` is ever called on this `Session`
        (`self._context_files_seeded` still `False`), `_build_context_files_interjection()` is
        consulted once; a non-`None` result is wrapped in a `<SystemInterjection
        subject="ProjectGuidance">` tag and prepended last, so it ends up as the outermost —
        i.e. first-read — preamble to the whole prompt, ahead of the `PermissionFramework`/
        standing interjections above. `self._context_files_seeded` is then set unconditionally,
        so this never runs again for the rest of the `Session`'s lifetime, matching every other
        one-shot interjection's "fires once" contract — see docs/specs/workspace-context-files.md.
        """
        tokens_before = self._tokens_recorded_so_far()
        if self._pending_permission_framework_interjection is not None:
            interjection = _wrap_system_interjection(
                "PermissionFramework", self._pending_permission_framework_interjection)
            prompt = f"{interjection}\n{prompt}"
            self._pending_permission_framework_interjection = None
        for subject in sorted(self._standing_interjection_providers):
            message = self._standing_interjection_providers[subject]()
            if message is not None:
                prompt = f"{_wrap_system_interjection(subject, message)}\n{prompt}"
        if not self._context_files_seeded:
            self._context_files_seeded = True
            project_guidance = self._build_context_files_interjection()
            if project_guidance is not None:
                prompt = f"{_wrap_system_interjection('ProjectGuidance', project_guidance)}\n{prompt}"
        user_message = Message(
            content=prompt,
            role="user",
            num_tokens=0,
            estimated_tokens=estimate_tokens(prompt),
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

        Passes no `on_permission_ask`/`on_tool_call_limit_reached`/`on_ask_user_questions`
        callbacks — there's no interactive surface to ask through here — so any `"ask"`
        permission verdict resolves per `config.permission_framework` (see
        `SessionConfig.permission_framework`), hitting a tool-call limit always raises
        `ToolCallLimitExceeded`, and an `AskUserQuestions` tool call always fails closed (there
        is nobody to ask). `send_turn()` already
        loops through every tool-call round on its own until the model returns a final
        plain-text reply, so this call runs the whole task to completion, not just one
        model round trip.
        """
        return self.send_turn(
            prompt, TurnEventHandlers(on_chunk=on_chunk, on_thinking_chunk=on_thinking_chunk))
