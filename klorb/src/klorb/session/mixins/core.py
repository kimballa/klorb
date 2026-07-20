# © Copyright 2026 Aaron Kimball
"""`SessionCoreMixin`: `Session.__init__`, its simple property accessors, message/statistics
loading, teardown/standing-interjection registration, and the active-model/token-accounting
helpers every other mixin builds on."""

import threading
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from klorb.api_provider import ApiProvider
from klorb.message import Message
from klorb.models.model import Model
from klorb.models.registry import ModelRegistry
from klorb.openrouter import OpenRouterApiProvider
from klorb.role import Role, get_role
from klorb.session.config import SessionConfig
from klorb.session.constants import (
    PERMISSION_FRAMEWORK_INTERJECTIONS,
    THINKING_EFFORT_TOKEN_BUDGETS,
    PermissionFramework,
    ThinkingEffort,
    generate_session_id,
)
from klorb.session.mixins._base import SessionBase
from klorb.session_statistics import SessionStatistics
from klorb.system_prompt import SystemPrompt
from klorb.tool_call_log import tool_call_logging_enabled
from klorb.tools.scratchpad.common import Scratchpad

if TYPE_CHECKING:
    # isort: off
    # `ToolRegistry` (via `ToolSetupContext`) depends on `ProcessConfig`, which itself
    # depends on `SessionConfig` from `klorb.session.config` — importing it for real here
    # would be circular. `Session` only stores and calls methods on a `ToolRegistry` it's
    # handed, so a type-checking-only import is enough (see
    # docs/adrs/tool-setup-context-carries-process-and-session-config.md).
    from klorb.tools.registry import ToolRegistry
    # `ProcessConfig` depends on `SessionConfig`/`ThinkingEffort`/`THINKING_EFFORT_TOKEN_BUDGETS`
    # from `klorb.session`, so importing it for real here would be circular too. `Session`
    # stores (and reads a couple of fields off) a `ProcessConfig` it's handed, but never
    # constructs one itself, so a type-checking-only import is enough, same as `ToolRegistry`
    # above.
    from klorb.process_config import ProcessConfig
    # `klorb.session` (this package's own `__init__.py`) assembles `Session` from this mixin,
    # so importing it for real here would be circular; needed only to `cast` `self` to
    # `Session` below, since `ToolRegistry.session` is typed against the assembled class, not
    # any one mixin.
    from klorb.session import Session
    # isort: on


class SessionCoreMixin(SessionBase):
    """`Session.__init__` plus the accessors, loaders, and lifecycle methods that don't belong
    to prompt/skill setup, permission resolution, tool execution, or turn dispatch -- see the
    other mixins in `klorb.session.mixins` for those."""

    def __init__(
        self,
        config: SessionConfig,
        provider: ApiProvider | None = None,
        model_registry: ModelRegistry | None = None,
        session_id: str | None = None,
        session_name: str | None = None,
        root_id: str | None = None,
        tool_registry: "ToolRegistry | None" = None,
        process_config: "ProcessConfig | None" = None,
        scratchpad_path: str | None = None,
    ) -> None:
        self.config = config
        self.id = session_id or generate_session_id()
        self.root_id = root_id or self.id
        """The `id` of the root (top-level) session this one descends from -- itself, for every
        session today, since klorb has no subagent-spawning mechanism yet (see `klorb.role.Role.
        repertoire`). A future subagent's `Session` would pass its parent's `root_id` through
        here so both share one identity for anything scoped to "the whole task tree" rather than
        this one `Session` specifically -- see `get_chainlink_label()`. Round-trips through
        `last-session.json` (`klorb.workspace.last_session.LastSessionState.root_id`) like `id`/
        `session_name`."""
        self._session_name: str | None = session_name
        self._role = get_role(config.role_name)
        self._provider = provider or OpenRouterApiProvider()
        self._model_registry = model_registry or ModelRegistry()
        self._system_prompt = SystemPrompt(config, self._role, self._model_registry, process_config)
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
            tool_registry.session = cast("Session", self)
        self.cur_chainlink_task_id: int | None = None
        """The chainlink issue id `TodoNext` most recently selected as this session's current
        task, or `None` if none is set (no `TodoNext` call yet, or the last one found nothing
        ready/open). Read by `klorb.tools.tasks.todo_next`'s standing interjection provider on
        every turn, and by `TodoCreate`'s `blocks_current_issue` argument. Round-trips through
        `last-session.json` (`klorb.workspace.last_session.LastSessionState.
        cur_chainlink_task_id`) like `id`/`session_name`. Written only via `set_chainlink_task()`,
        never assigned directly by a `Tool`. See docs/specs/chainlink-task-tracking.md."""
        self.tool_state: dict[str, Any] = {}
        """Per-session runtime state a `Tool` implementation wants to keep across calls within
        this one session (e.g. `BashTool`'s one-time sandbox-fallback notice), keyed by tool
        name (`tool_state["Bash"]`). Distinct from `config` (`SessionConfig`, user-configurable
        settings only): this is ad hoc, tool-private bookkeeping, never read or written by
        `Session` itself, and never persisted to disk. A `Tool` accesses it via
        `self.context.session.tool_state` (see `klorb.tools.setup_context.ToolSetupContext.
        session`) and must `.setdefault(...)` its own key rather than assuming it's pre-populated
        — this dict starts empty for every new `Session`."""
        self.active_cancel_event: threading.Event | None = None
        """The in-flight turn's `TurnEventHandlers.cancel_event`, or `None` when no turn is
        running — set at the top of `_dispatch_turn` and cleared in its `finally` once the turn
        ends. Distinct from `tool_state`: this is how a currently-executing `Tool.apply()` call
        (running synchronously on the same worker thread as `_dispatch_turn`, via
        `self.context.session.active_cancel_event`) discovers the same `threading.Event` a
        Ctrl+C/Escape interrupt sets, so a tool that owns a real subprocess (`BashTool`) can react
        to cancellation immediately — e.g. sending `SIGINT` to a running shell command — rather
        than only at the next tool-call/round boundary `_dispatch_turn`/`_run_tool_calls`
        themselves check. See docs/specs/interrupt-and-liveness-watchdog.md."""
        self._tool_calls_this_session = 0
        self._tool_calls_this_turn = 0
        self._compatibility_claude_markdown = (
            process_config.compatibility_claude_markdown if process_config is not None else False
        )
        self._compatibility_claude_skills = (
            process_config.compatibility_claude_skills if process_config is not None else False
        )
        """Whether to also discover workspace-namespace skills from `.claude/skills/`. See
        docs/specs/skills.md."""
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
        self._skills_seeded = False
        """Whether `send_turn()` has already computed the one-shot `AvailableSkills`
        `<SystemInterjection>` for the first turn (see `_build_available_skills_interjection`). Set
        unconditionally the first time it's checked, so the standing list is locked for the
        session."""
        self._context_files_seeded = False
        """Whether `send_turn()` has already computed (and, if non-`None`, prepended) the
        one-shot `ProjectGuidance` `<SystemInterjection>` carrying the workspace's
        context-instruction files (`.klorb/INSTRUCTIONS.md`, `AGENTS.md`, and `CLAUDE.md` when
        compatibility is enabled) onto the very first turn's prompt — see
        `_build_context_files_interjection()`. Set unconditionally the first time it's
        checked, even when there was nothing to prepend (workspace untrusted, or no applicable
        file exists on disk), so a later turn never re-reads the filesystem or re-prepends
        anything."""
        self._metadata_seeded = False
        """Whether `send_turn()` has already prepended the one-shot `Metadata`
        `<SystemInterjection>` carrying the session start time and workspace root name onto
        the very first turn's prompt. Set unconditionally the first time it's checked."""
        self._session_started_at = datetime.now()
        """Timestamp recorded at session construction, used as the session's start time in the
        one-shot `Metadata` interjection prepended to the first user message."""
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
        self.statistics = SessionStatistics()
        """Running tally of message counts, tool-call counts, and per-tool success/failure
        breakdowns, updated incrementally as turns flow through `send_turn()` /
        `_send_and_receive()` / `_run_tool_calls()`. Persisted alongside the session state
        (see `klorb.workspace.last_session.LastSessionState.statistics`) so a restored session
        continues accumulating from where the previous one left off."""

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

    @property
    def name(self) -> str | None:
        """The human-readable title assigned by the session-naming classifier, or `None`
        if no name has been assigned yet (e.g. fresh session, or naming failed)."""
        return self._session_name

    @name.setter
    def name(self, value: str | None) -> None:
        self._session_name = value

    def get_chainlink_label(self) -> str:
        """Return the `chainlink` label every `klorb.tools.tasks.common.ChainlinkClient`
        operation for this session is scoped to: `root_id`, not `id` -- a future subagent's
        `Session` shares its root's `id` here, so a subagent's issues stay associated with the
        same task tree its root session tracks rather than being scoped to the subagent's own,
        narrower `id`. See docs/specs/chainlink-task-tracking.md."""
        return self.root_id

    def set_chainlink_task(self, task_id: int | None) -> None:
        """Set `cur_chainlink_task_id` -- the only way a caller (`klorb.tools.tasks.todo_next.
        TodoNextTool`) should change it, rather than assigning the attribute directly."""
        self.cur_chainlink_task_id = task_id

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

    def load_statistics(self, statistics: SessionStatistics) -> None:
        """Replace this session's running statistics with `statistics` — e.g. restoring a
        previous interactive session's counts from `klorb.workspace.last_session`. Intended
        to be called once, immediately after construction (alongside `load_messages()`),
        before any `send_turn()`, so that new increments continue from where the previous
        session left off rather than starting from zero.
        """
        self.statistics = statistics

    def set_permission_framework(self, value: PermissionFramework) -> None:
        """Change `config.permission_framework` to `value` and queue a system-harness
        interjection (`PERMISSION_FRAMEWORK_INTERJECTIONS[value]`) for `send_turn()` to
        prepend onto the prompt of the next turn it dispatches. Callers that let the user
        change this mode mid-conversation (e.g. `klorb.tui.ReplApp`'s Shift+Tab
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

        Callers that replace a `Session` outright (`klorb.tui.ReplApp.clear_session()`) must
        call this on the outgoing `Session` first — nothing else tears it down, and a live
        `subprocess.Popen` handle would otherwise leak for the rest of the klorb process's
        lifetime. See docs/plans/archive/005-session-scoped-bash-terminals.md.
        """
        for teardown in self._teardown_callbacks.values():
            teardown()
        self._teardown_callbacks.clear()

    def active_model(self) -> Model | None:
        """Return the registered `Model` for `config.model`, or `None` if it isn't registered
        (e.g. `--model` targets an OpenRouter identifier with no `Model` implementation)."""
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
        model = self.active_model()
        return model.name() if model is not None else self.config.model

    def total_tokens_used(self) -> int:
        """Return the total tokens that would be uploaded to the model at the start of the
        next turn: each message's own client-side `num_tokens` (a `tiktoken` estimate of its
        content, see `Message.num_tokens`), summed over every message that would actually be
        sent. `"thinking"`-role messages are excluded exactly when `OpenRouterApiProvider.
        _build_api_messages()` would exclude them -- when the active model's
        `Model.drop_reasoning()` is `True` -- and included otherwise, since preserved
        reasoning is resent on the next turn by default. A stored `"system"`/`"tool_defs"`
        bookkeeping message is itself skipped from the literal request body (see
        `_ensure_system_message`/`_ensure_tool_defs_message`), but equivalent content is
        always resent out-of-band via `send_prompt(system_prompt=..., tools=...)`, so it
        still counts here. See docs/adrs/count-every-message-tokens-client-side-with-
        tiktoken.md.
        """
        drop_reasoning = self._drop_reasoning()
        return sum(
            message.num_tokens for message in self._messages
            if message.role != "thinking" or not drop_reasoning
        )

    def total_output_tokens_used(self) -> int:
        """Return the running total of tokens the model has generated so far: each
        `role="assistant"`/`"tool_use"`/`"thinking"` message's own client-side `num_tokens`
        (see `Message.num_tokens`), summed unconditionally -- unlike `total_tokens_used()`,
        this reports everything the model has ever produced, regardless of whether the
        active model's `Model.drop_reasoning()` would keep a `"thinking"` message out of a
        later upload."""
        return sum(
            message.num_tokens for message in self._messages
            if message.role in ("assistant", "tool_use", "thinking")
        )

    def max_context_window(self) -> int | None:
        """Return the active model's max context window in tokens, or `None` if the
        active model isn't registered or doesn't report one.
        """
        model = self.active_model()
        if model is None:
            return None
        return model.capabilities().get("max_context_window")
