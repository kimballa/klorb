# © Copyright 2026 Aaron Kimball
"""`SessionCoreMixin`: `Session.__init__`, its simple property accessors, message/statistics
loading, teardown/standing-interjection registration, and the active-model/token-accounting
helpers every other mixin builds on."""

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from klorb.api_provider import ApiProvider
from klorb.images.prepare import ImagePipelineConfig
from klorb.lockfile import Lockfile
from klorb.message import Message
from klorb.models.model import Model
from klorb.models.registry import ModelRegistry
from klorb.openrouter import OpenRouterApiProvider
from klorb.role import Role, get_role
from klorb.session.config import PermissionFrameworkState, SessionConfig
from klorb.session.constants import (
    PERMISSION_FRAMEWORK_INTERJECTIONS,
    THINKING_EFFORT_TOKEN_BUDGETS,
    PermissionFramework,
    ThinkingEffort,
    generate_session_id,
)
from klorb.session.events import QueuedMessage, TurnEventHandlers
from klorb.session.mixins._base import SessionBase
from klorb.session_naming import (
    SessionName,
    default_naming_model,
    fallback_session_title,
    generate_session_name,
    rename_session_id,
    thinking_effort_for,
)
from klorb.session_statistics import SessionStatistics
from klorb.system_prompt import SystemPrompt
from klorb.token_estimate import estimate_tokens
from klorb.tool_call_log import tool_call_logging_enabled
from klorb.tools.scratchpad.common import Scratchpad
from klorb.tools.skill.catalog import SkillCatalogRegistry

if TYPE_CHECKING:
    # isort: off
    # `ToolRegistry` (via `ToolSetupContext`) depends on `ProcessConfig`, which itself
    # depends on `SessionConfig` from `klorb.session.config` — importing it for real here
    # would be circular. `Session` only stores and calls methods on a `ToolRegistry` it's
    # handed, so a type-checking-only import is enough (see
    # docs/adrs/00022-tool-setup-context-carries-process-and-session-config.md).
    from klorb.tools.registry import ToolRegistry
    # `ProcessConfig` depends on `SessionConfig`/`ThinkingEffort`/`THINKING_EFFORT_TOKEN_BUDGETS`
    # from `klorb.session`, so importing it for real here would be circular too. `Session`
    # stores (and reads a couple of fields off) a `ProcessConfig` it's handed, but never
    # constructs one itself, so a type-checking-only import is enough, same as `ToolRegistry`
    # above.
    from klorb.process_config import ProcessConfig
    from klorb.tools.util.read_file_core import ReadFileCore
    # `klorb.session` (this package's own `__init__.py`) assembles `Session` from this mixin,
    # so importing it for real here would be circular; needed only to `cast` `self` to
    # `Session` below, since `ToolRegistry.session` is typed against the assembled class, not
    # any one mixin.
    from klorb.session import Session
    # `klorb.agents.runtime` imports `klorb.session.mixins.turns` (for `wrap_system_interjection`)
    # -- a real import here, while `klorb.session`'s own `__init__.py` is still assembling this
    # very mixin, would be circular. See `_create_subagent_tracker`/`close`'s own deferred
    # imports, which is where this type is actually used for real.
    from klorb.agents.runtime import SubagentTracker
    # `klorb.tools.tasks.common` reaches back into `klorb.session` (via `ToolSetupContext`), so
    # importing it for real here would be circular; needed only to type `ensure_chainlink_client`'s
    # return value, which is constructed via a deferred import where it's actually used.
    from klorb.tools.tasks.common import ChainlinkClient
    # `klorb.hooks.dispatcher` (which `_dispatch_hook` imports for real, deferred, where it's
    # actually used) depends on `klorb.session.config`, so a real import here would be circular;
    # needed only to type `_dispatch_hook`'s return value.
    from klorb.hooks.config import EventConfig, FileSystemModifiedEventConfig, TimerEventConfig
    from klorb.hooks.wire import EventInput, HookOutput
    # isort: on

logger = logging.getLogger(__name__)


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
        aliases: list[str] | None = None,
        last_modified_at: datetime | None = None,
        tool_registry: "ToolRegistry | None" = None,
        process_config: "ProcessConfig | None" = None,
        scratchpad_path: str | None = None,
        parent: "Session | None" = None,
        effective_subagent_roles: frozenset[str] = frozenset(),
        max_output_tokens: int | None = None,
    ) -> None:
        self.config = config
        self.id = session_id or generate_session_id()
        self.root_id = root_id or self.id
        """The `id` of the root (top-level) session this one descends from -- itself, for every
        top-level session, or `parent.root_id` for a subagent (see `parent`). Anything scoped to
        "the whole task tree" rather than this one `Session` specifically keys off this, not
        `id` -- see `get_chainlink_label()`. Round-trips through `session.json` (`klorb.
        workspace.session_store.SessionState.root_id`) like `id`/`session_name`."""
        self.parent = parent
        """The `Session` this one was spawned from as a subagent, or `None` for a top-level
        session -- see docs/specs/subagents.md's "Subagent session model". Not
        persisted: a subagent session is never written to its own `sessions/<subdir>/` (see
        that spec's "Persistence" section), so this only ever needs to be live in-process."""
        self.depth = parent.depth + 1 if parent is not None else 0
        """How many subagent hops below the top-level (user-facing) session this one sits --
        `0` for that top-level session itself. `CreateSubagent` rejects a call that would
        produce a child whose `depth` exceeds `ProcessConfig.subagents_max_depth`."""
        self.effective_subagent_roles = effective_subagent_roles
        """The subagent role names this session may itself pass to `CreateSubagent`, computed
        once at this session's own construction: for a subagent, via `klorb.agents.intersection.
        compute_child_subagent_roles` against its parent's own `effective_subagent_roles`; for a
        root session, via `klorb.agents.policy.compute_root_session_grants` against every role
        `agents.json` defines. Never a fresh `agents.json` lookup of this session's own role name
        at read time, so an intervening level's narrowing can't be recovered further down the
        chain. Defaults to an empty `frozenset` -- a `Session` constructed without explicitly
        computing this (e.g. most unit tests) may launch no subagents at all, rather than
        silently inheriting "everything"."""
        self._max_output_tokens = max_output_tokens
        """Per-request `max_tokens` cap threaded into every `ApiProvider.send_prompt()` call
        this session makes -- `None` (the default) applies no cap beyond the model's own.
        Set from `CreateSubagent`'s `max_output_tokens` arg for a subagent `Session`; not a
        `klorb-config.json` setting, since it's a one-off per-subagent budget the *creating*
        agent chooses, not a standing preference. See docs/specs/subagents.md."""
        self._next_child_index = 0
        """Count of subagents this session has ever spawned, monotonically incremented by
        `_allocate_child_index()` and never decremented or reused -- even after a child
        finishes or is torn down -- so a dotted-decimal `address()` segment always identifies
        one specific subagent for as long as this process runs. See `address()`."""
        self._child_index = parent._allocate_child_index() if parent is not None else 0
        """This session's own address segment among its parent's children (assigned once, by
        the parent, at construction time), or `0` for a top-level session -- see `address()`."""
        if parent is None:
            # `config` (built via `process_config.session.model_copy()`, or restored from
            # `session.json`) may still reference the same `PermissionFrameworkState` box
            # another top-level session shares, since pydantic's `model_copy()` doesn't
            # deep-copy nested models -- without this, every session built from the same
            # `ProcessConfig.session` template would silently cycle each other's permission
            # framework. A subagent (`parent is not None`) keeps whatever box its `config.
            # model_copy()` from the parent's own `config` already carries, by design.
            self.config.permission_framework_state = PermissionFrameworkState(
                value=self.config.permission_framework_state.value)
        self.aliases: list[str] = list(aliases) if aliases is not None else []
        """Every prior `id` this session was renamed from (oldest first) -- appended to by
        `set_id`, never assigned directly. Lets a caller holding a pre-rename id (e.g. an ACP
        client that recorded the id `session/new` returned before the session-naming classifier
        renamed it) still resolve this session on disk; round-trips through `session.json` and
        `sessions.json` (`klorb.workspace.session_store.SessionState.aliases`/
        `RecentSession.aliases`) like `id`/`session_name`."""
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
        docs/adrs/00045-session-applies-its-own-permission-grants.md."""
        self._mention_read_file_core = self._create_read_file_core(
            process_config.mention_max_lines
            if process_config is not None else self._default_mention_max_lines(),
            process_config.read_file_max_line_length
            if process_config is not None else self._default_mention_max_line_length(),
        )
        self._image_pipeline_config = self._create_image_pipeline_config(process_config)
        """This session's `ImagePipelineConfig`, built once from `process_config`'s
        `tools.images.*` settings (or the packaged defaults) -- passed to `klorb.images.prepare.
        prepare_image_for_model` for every image this session sends, whether it arrived as a
        drag-drop/paste attachment (`klorb.server.klorb_agent._extract_prompt_content`, via the
        `image_pipeline_config` property below) or an @mentioned file recognized as an image
        (`resolve_at_mentions()`, see docs/specs/at-mention-file-inlining.md)."""
        self._thinking_token_budgets = (
            process_config.thinking_token_budgets if process_config is not None
            else THINKING_EFFORT_TOKEN_BUDGETS
        )
        self._tool_registry = tool_registry
        if tool_registry is not None:
            tool_registry.session = cast("Session", self)
        self.cur_chainlink_task_id: int | None = None
        """The chainlink issue id most recently selected as this session's current task -- by
        `TodoNext`, or by `TodoUpdate`/`TodoCreate`'s auto-activation (`klorb.tools.tasks._util.
        maybe_activate_task`) -- or `None` if none is set (no task picked or activated yet, or
        the last one found nothing ready/open). Read by `klorb.tools.tasks._util`'s standing
        interjection provider on every turn, and by `TodoCreate`'s `blocks_current_issue`
        argument. Round-trips through `session.json` (`klorb.workspace.session_store.SessionState.
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
        self._skill_catalog_registry = SkillCatalogRegistry()
        """This session's own skill catalogs -- a fresh, empty `SkillCatalogRegistry` per
        `Session`, rescanning the disk on first use (`_ensure_skill_catalog`) rather than reusing
        another session's catalog. Not persisted -- a restored session rebuilds it the same way a
        fresh one does. See `klorb.tools.skill.catalog` and docs/specs/skills.md."""
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
        self._agent_group_seeded = False
        """Whether `send_turn()` has already prepended the one-shot `AgentGroup`
        `<SystemInterjection>` (a markdown table of every agent in this session's group) onto
        the very first turn's prompt -- see `_build_agent_group_interjection()`. Set
        unconditionally the first time it's checked, even for a root session (which has no
        group to report, so nothing is ever prepended for it)."""
        self._session_naming_pending = self._session_name is None
        """Whether `send_turn()` still needs to run the one-shot session-naming classifier (see
        `_run_session_naming`) on its first call. Seeded `False` when a `session_name` was
        already supplied to this constructor (a restored session that was already named), `True`
        otherwise (a fresh session, or a restored session that was never successfully named).
        Set unconditionally the first time `send_turn()` checks it, regardless of the
        classifier's outcome, so a failed/timed-out attempt is never retried."""
        self._session_started_at = datetime.now()
        """Timestamp recorded at session construction, used as the session's start time in the
        one-shot `Metadata` interjection prepended to the first user message."""
        self._last_modified_at = last_modified_at
        """When this session was last saved to `session.json`, or `None` if it hasn't been saved
        yet (or was restored from a save file that predates this field). Restored from
        `SessionState.last_modified_timestamp` if given; otherwise stamped with `datetime.now()`
        by `SessionPersistenceMixin._write_session_state_and_touch()` on every save, and
        round-trips through `session.json`/`sessions.json` (`RecentSession.
        last_modified_timestamp`) like `id`/`session_name`."""
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
        self._queued_messages: list[QueuedMessage] = []
        """User messages typed during an active agent turn, awaiting delivery as the next
        user turn when the current turn ends. Enqueued by the TUI when the user presses
        Enter while `_turn_in_flight` is `True`; drained by `_finish_turn()` or
        `_run_tool_calls()`. An empty list means no pending messages."""
        self._user_msg_event: threading.Event = threading.Event()
        """Signaled by `enqueue_queued_message()` whenever a user message is queued during an
        active turn. Watched by `UserInterruptibleTool` subclasses (e.g.
        `WaitForSubagentTool`) so a blocking wait can be interrupted immediately when the user
        sends a new message, rather than waiting for the next cancel-event poll cycle."""
        self._current_turn_handlers: TurnEventHandlers | None = None
        """The `TurnEventHandlers` for the currently active turn, or `None` when no turn is
        running. Set at the top of `_dispatch_turn` and cleared in its `finally`. Used by
        `enqueue_queued_message` and `drain_queued_messages` to call the `on_enqueue_message`
        and `on_send_queued_message` hooks."""
        self._current_turn_mentioned_skill_ids: frozenset[tuple[str, str]] = frozenset()
        """Every skill `(namespace, name)` the current turn's raw prompt referenced via a
        `/<name>` token, resolved against the typed catalog -- recomputed at the top of every
        `send_turn()` call. Consulted by `fire_activate_skill_hook` to report `HookInput.
        is_user_mentioned` when the model later calls `ActivateSkill` mid-turn."""
        self._current_turn_leading_skill_id: tuple[str, str] | None = None
        """The skill `(namespace, name)` the current turn's raw prompt *began* with (a `/<name>`
        reference), or `None` -- a strict subset of `_current_turn_mentioned_skill_ids`.
        Consulted by `fire_activate_skill_hook` to report `HookInput.is_user_activated`."""
        self._chained_hook_turns = 0
        """How many turns in a row `start_turn_or_enqueue` has started back-to-back on behalf of
        a `chat` hook/event handler, without an intervening real user- or tool-driven turn --
        see `start_turn_or_enqueue` and `SessionConfig.max_chained_hook_turns`."""
        self._dispatching_chained_turn = False
        """`True` for the duration of a `send_turn()` call `start_turn_or_enqueue` itself made,
        so that call doesn't reset `_chained_hook_turns` back to `0` the way an ordinary,
        externally-driven `send_turn()` call does -- see `start_turn_or_enqueue`."""
        self.on_clear_session_requested: Callable[[str], None] | None = None
        """Set by whichever host owns this session's lifecycle (e.g. `klorb.tui.ReplApp`) to
        replace it with a fresh session seeded by the given message, in response to a hook's
        `HookOutput.clear_session` -- see `close()` and `SessionTurnsMixin.
        _fire_agent_turn_end_hook`. `None` (the default) means no host has registered one, e.g.
        headless/ACP or most unit tests; a `clear_session` request against such a session
        degrades to a warning rather than being fulfilled."""
        self.subagent_tracker = self._create_subagent_tracker()
        """Bookkeeping for the subagents this session has directly created -- see
        `klorb.agents.runtime.SubagentTracker`. Every `Session` gets its own, fresh and empty,
        including a subagent's own child `Session` (a subagent may itself create further
        subagents, up to `ProcessConfig.subagents_max_depth`)."""
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
        (see `klorb.workspace.session_store.SessionState.statistics`) so a restored session
        continues accumulating from where the previous one left off."""
        self._session_lock: Lockfile | None = None
        """The `session.lock` held on this session's `sessions/<subdir>/` directory, or `None`
        if this session hasn't claimed one yet (or ever will -- an untrusted workspace never
        claims one). Set by `SessionPersistenceMixin.claim_session_directory`, released and
        cleared by `SessionPersistenceMixin._finalize_session_persistence` (called from
        `close()`)."""
        self._session_subdir: str | None = None
        """The `sessions/<subdir>/` name this session's state is saved under, or `None` before
        `claim_session_directory` has run. Distinct from `self.id`: set once, to `self.id`'s
        value *at claim time*, and never renamed afterward even if `self.id` later changes --
        see `klorb.workspace.session_store.RecentSession.subdir`."""
        self._session_claimed = False
        """Whether this session currently owns a `sessions/<subdir>/` directory (holds its
        `session.lock`) -- see `SessionPersistenceMixin.claim_session_directory`."""

    @staticmethod
    def _create_read_file_core(max_lines: int, max_line_length: int) -> "ReadFileCore":
        """Construct a `ReadFileCore`, deferring the import to avoid a circular dependency
        through `klorb.tools.util` → `klorb.tools.setup_context` → `klorb.process_config` →
        `klorb.session`."""
        from klorb.tools.util.read_file_core import ReadFileCore
        return ReadFileCore(max_lines, max_line_length)

    @staticmethod
    def _create_subagent_tracker() -> "SubagentTracker":
        """Construct a `SubagentTracker`, deferring the import since `klorb.agents.runtime`
        itself imports `klorb.session.mixins.turns` (for `wrap_system_interjection`) -- a
        module-level import here, while `klorb.session`'s own `__init__.py` is still assembling
        this very mixin, would be circular."""
        from klorb.agents.runtime import SubagentTracker
        return SubagentTracker()

    @staticmethod
    def _create_image_pipeline_config(
        process_config: "ProcessConfig | None",
    ) -> ImagePipelineConfig:
        """Build this session's `ImagePipelineConfig` from *process_config*'s `tools.images.*`
        settings, or the packaged defaults (imported lazily to avoid the same circular
        dependency `_create_read_file_core` avoids) for a `Session` built without a
        `ProcessConfig`."""
        if process_config is not None:
            return ImagePipelineConfig(
                default_max_dimension_px=process_config.image_default_max_dimension_px,
                max_bytes_raw=process_config.image_max_bytes_raw,
                preferred_formats=process_config.image_preferred_formats,
            )
        from klorb.process_config import (
            DEFAULT_IMAGE_DEFAULT_MAX_DIMENSION_PX,
            DEFAULT_IMAGE_MAX_BYTES_RAW,
            DEFAULT_IMAGE_PREFERRED_FORMATS,
        )
        return ImagePipelineConfig(
            default_max_dimension_px=DEFAULT_IMAGE_DEFAULT_MAX_DIMENSION_PX,
            max_bytes_raw=DEFAULT_IMAGE_MAX_BYTES_RAW,
            preferred_formats=list(DEFAULT_IMAGE_PREFERRED_FORMATS),
        )

    @staticmethod
    def _default_mention_max_lines() -> int:
        """`DEFAULT_MENTION_MAX_LINES`, imported lazily to avoid the same circular dependency
        `_create_read_file_core` avoids -- `klorb.process_config` imports from `klorb.session`,
        so a module-level import here would be circular."""
        from klorb.process_config import DEFAULT_MENTION_MAX_LINES
        return DEFAULT_MENTION_MAX_LINES

    @staticmethod
    def _default_mention_max_line_length() -> int:
        """`DEFAULT_READ_FILE_MAX_LINE_LENGTH`, imported lazily to avoid the same circular
        dependency `_create_read_file_core` avoids."""
        from klorb.process_config import DEFAULT_READ_FILE_MAX_LINE_LENGTH
        return DEFAULT_READ_FILE_MAX_LINE_LENGTH

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
    def image_pipeline_config(self) -> ImagePipelineConfig:
        """Return this session's `ImagePipelineConfig` -- see `_create_image_pipeline_config`.
        The single source both `resolve_at_mentions()` and `klorb.server.klorb_agent.
        _extract_prompt_content` (a drag-drop/paste ACP attachment) pass to `klorb.images.
        prepare.prepare_image_for_model`, so every image this session sends is resized/
        transcoded under the same settings regardless of how it arrived."""
        return self._image_pipeline_config

    @property
    def tool_registry(self) -> "ToolRegistry | None":
        """Return the `ToolRegistry` this session dispatches model tool-call requests
        through, or `None` if tool-calling isn't enabled for this session."""
        return self._tool_registry

    @property
    def skill_catalog_registry(self) -> SkillCatalogRegistry:
        """Return this session's own `SkillCatalogRegistry` -- see `klorb.tools.skill.catalog`."""
        return self._skill_catalog_registry

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
        """The human-readable session title, assigned by the session-naming classifier or
        a user rename, or `None` if no title has been assigned (e.g. fresh session)."""
        return self._session_name

    @name.setter
    def name(self, value: str | None) -> None:
        self._session_name = value

    @property
    def session_naming_pending(self) -> bool:
        """Whether `send_turn()` still needs to run the one-shot session-naming classifier on
        its first call -- see `_run_session_naming`. A caller (e.g. the TUI) reads this before
        calling `send_turn()` to know whether to expect `TurnEventHandlers.on_session_name_changed`
        to fire for that call."""
        return self._session_naming_pending

    @session_naming_pending.setter
    def session_naming_pending(self, value: bool) -> None:
        self._session_naming_pending = value

    def set_id(self, new_id: str) -> None:
        """The only sanctioned way to change `self.id`. If `id == root_id` (this session hasn't
        diverged from its root -- true for every session today, since klorb has no
        subagent-spawning mechanism yet), `root_id` is updated to `new_id` too, keeping both in
        sync; otherwise only `id` changes. The previous `id` is recorded in `self.aliases` so a
        caller holding it can still resolve this session later. Callers must never assign
        `self.id`/`self.root_id` directly -- see `get_chainlink_label()` for why `root_id`
        staying in sync with a renamed `id` matters."""
        if new_id != self.id and self.id not in self.aliases:
            self.aliases.append(self.id)
        if self.id == self.root_id:
            self.root_id = new_id
        self.id = new_id

    def _run_session_naming(self, prompt_text: str) -> "SessionName | None":
        """Derive a `SessionName` from `prompt_text` (this session's first submitted prompt) via
        `klorb.session_naming.generate_session_name`, and on success rename this session's `id`
        (via `set_id`, preserving its timestamp prefix and swapping in the derived slug) and set
        `name` to the derived title. On failure (the classifier times out, is unavailable, or
        never returns a valid reply), `name` is instead set to
        `klorb.session_naming.fallback_session_title(prompt_text)` -- `id` is left alone in that
        case, since there's no `SessionName.slug` to rename it with. Returns the classifier's
        result (or `None` on failure) for the caller's `TurnEventHandlers.on_session_name_changed`
        to react to; never raises, matching `generate_session_name`'s own "never raises" contract.
        Falls back to `DEFAULT_SESSION_CLASSIFIER_TIMEOUT_SECONDS`/`_E2E_TIMEOUT_SECONDS` and
        `default_naming_model(self)` when this session has no `ProcessConfig`
        (`self._process_config is None`)."""
        # Deferred: `klorb.process_config` imports from `klorb.session`, so a module-level
        # import here would be circular.
        from klorb.process_config import (
            DEFAULT_SESSION_CLASSIFIER_E2E_TIMEOUT_SECONDS,
            DEFAULT_SESSION_CLASSIFIER_TIMEOUT_SECONDS,
        )

        model = (
            self._process_config.session_classifier_model
            if self._process_config is not None and self._process_config.session_classifier_model
            else default_naming_model(cast("Session", self))
        )
        timeout = (
            self._process_config.session_classifier_timeout_seconds
            if self._process_config is not None
            else DEFAULT_SESSION_CLASSIFIER_TIMEOUT_SECONDS
        )
        e2e_timeout = (
            self._process_config.session_classifier_e2e_timeout_seconds
            if self._process_config is not None
            else DEFAULT_SESSION_CLASSIFIER_E2E_TIMEOUT_SECONDS
        )
        reasoning = thinking_effort_for(cast("Session", self), model)
        result = generate_session_name(
            prompt_text, api_provider=self._provider, model=model, timeout=timeout,
            e2e_timeout=e2e_timeout, reasoning=reasoning)
        if result is not None:
            self.set_id(rename_session_id(self.id, result.slug))
            self.name = result.title
        else:
            self.name = fallback_session_title(prompt_text)
        return result

    def get_chainlink_label(self) -> str:
        """Return the `chainlink` label every `klorb.tools.tasks.common.ChainlinkClient`
        list/close-all operation for this session is scoped to: `"group:<root_id>"`."""
        return f"group:{self.root_id}"

    def ensure_chainlink_client(self) -> "ChainlinkClient | None":
        """Return this session's `klorb.tools.tasks.common.ChainlinkClient`, constructing it on
        first call and caching it in `tool_state` -- idempotent and cheap after that, a plain
        dict lookup rather than re-attempting binary discovery or construction. Only a *root*
        session's own construction actually registers the group's close-time cleanup
        (`ChainlinkClient.__init__`'s `session.parent is None` check); calling this on a
        non-root session only pays `_ensure_setup()`'s cost, which is harmless since the group's
        teardown was already registered when the root created its first task-tracking subagent --
        the only session a task-tracking subagent can ultimately descend from. Used by
        `CreateSubagentTool` right before dispatching a new subagent whose own tool set includes a
        `TASKS` tool, so the teardown gets wired up even if the creating session never itself
        calls a `Todo*` tool -- see docs/specs/chainlink-task-tracking.md's "Setup" section.
        `None` if this session has no `ProcessConfig` (most unit tests), the chainlink binary
        isn't available, or construction failed -- any construction failure is logged at `debug`
        and swallowed, since a real `Todo*` tool call will surface the same failure for real
        later. Deferred imports: `klorb.tools.setup_context`/`klorb.tools.tasks.common` both
        reach back into `klorb.session`, so a module-level import here would be circular."""
        from klorb.tools.tasks.common import CHAINLINK_CLIENT_TOOL_STATE_KEY
        tasks_state = self.tool_state.get(CHAINLINK_CLIENT_TOOL_STATE_KEY)
        if tasks_state is not None and "client" in tasks_state:
            return cast("ChainlinkClient | None", tasks_state["client"])
        if self._process_config is None:
            return None
        from klorb.tools.tasks.common import chainlink_available
        tasks_state = self.tool_state.setdefault(CHAINLINK_CLIENT_TOOL_STATE_KEY, {})
        if not chainlink_available():
            tasks_state["client"] = None
            return None
        from klorb.tools.setup_context import ToolSetupContext
        from klorb.tools.tasks.common import ChainlinkClient
        context = ToolSetupContext(
            process_config=self._process_config, session_config=self.config,
            session=cast("Session", self))
        try:
            client: "ChainlinkClient | None" = ChainlinkClient(context)
        except Exception:
            logger.debug(
                "Best-effort ChainlinkClient init failed for session %s.", self.id, exc_info=True)
            client = None
        tasks_state["client"] = client
        return client

    def _build_agent_group_interjection(self) -> str | None:
        """Build the one-shot `AgentGroup` `<SystemInterjection>` body for a subagent's first
        turn: a markdown table of every agent in the same session tree the *creating* session can
        currently see -- role, id, and title, plus a `Relationship` column marking this session's
        own row `"(This is you)"` and its parent's row `"(Your parent)"`. `None` for a root
        session (`self.parent is None`), which has no group to report. Deferred import: `klorb.
        agents.runtime` itself imports `klorb.session.mixins.turns` at module level, so a
        module-level import here (while `klorb.session`'s own `__init__.py` is still assembling
        this mixin) would be circular."""
        if self.parent is None:
            return None
        from klorb.agents.runtime import walk_session_tree
        root = self.parent
        while root.parent is not None:
            root = root.parent
        rows = ["| Role | Id | Title | Relationship |", "| --- | --- | --- | --- |"]
        for node in walk_session_tree(root):
            session = node.session
            if session.id == self.id:
                relationship = "(This is you)"
            elif session.id == self.parent.id:
                relationship = "(Your parent)"
            else:
                relationship = ""
            title = session.name or ""
            rows.append(f"| {session.config.role_name} | {session.id} | {title} | {relationship} |")
        return "\n".join(rows)

    def _allocate_child_index(self) -> int:
        """Return the next `_child_index` for a subagent about to be constructed with
        `parent=self`. Called once per subagent, from that subagent's own `__init__` -- see
        `_next_child_index`."""
        self._next_child_index += 1
        return self._next_child_index

    def address(self) -> str:
        """Return this session's dotted-decimal display address within its session tree (e.g.
        `"1"` for a top-level session, `"1.2"` for its second subagent, `"1.2.1"` for that
        subagent's first subagent) -- recomputed from the live `parent` chain on every call
        rather than cached or persisted, per docs/specs/subagents.md's "Addressing"
        section. Purely a human-facing label for the agents panel; no tool takes an address as
        an argument."""
        if self.parent is None:
            return "1"
        return f"{self.parent.address()}.{self._child_index}"

    def set_chainlink_task(self, task_id: int | None) -> None:
        """Set `cur_chainlink_task_id` -- the only way a caller (`klorb.tools.tasks._util.
        set_current_task`, picking a new one on `TodoNextTool`'s or `TodoUpdateTool`'s/
        `TodoCreateTool`'s auto-activation's behalf, or `TodoUpdateTool` clearing it when the
        tracked task itself is closed) should change it, rather than assigning the attribute
        directly."""
        self.cur_chainlink_task_id = task_id

    def enqueue_queued_message(self, queued_msg: QueuedMessage) -> None:
        """Append `queued_msg` to the queue of user messages awaiting delivery as the next
        user turn when the current turn ends. Called by the TUI when the user presses Enter
        while an agent turn is in flight. If the current turn's `TurnEventHandlers` has an
        `on_enqueue_message` hook, it is called with `queued_msg` so the UI can create the
        italics "queued..." block and save widget references in `queued_msg.history_data`."""
        self._queued_messages.append(queued_msg)
        self._user_msg_event.set()
        if (self._current_turn_handlers is not None
                and self._current_turn_handlers.on_enqueue_message is not None):
            self._current_turn_handlers.on_enqueue_message(queued_msg)

    def drain_queued_messages(
        self, callbacks: TurnEventHandlers | None = None,
    ) -> list[QueuedMessage]:
        """Return every queued message currently in the queue and clear it. The returned
        messages are folded into a single new user turn by `_finish_turn()` (see its
        docstring for why one turn, not one per message) or delivered as individual
        `UserInterjectionPayload`s on tool-response envelopes by `_run_tool_calls()`.

        The `on_send_queued_message` hook, if present, is called with each drained message so
        the UI can remove its italics block from the history -- it does not, itself, submit
        anything; that's the caller's job with the returned list. Taken from `callbacks` when
        given; otherwise falls back to the current turn's
        `_current_turn_handlers`, for `_run_tool_calls()`'s mid-turn call (drained while
        `_dispatch_turn` is still on the stack, so `_current_turn_handlers` is still live). An
        explicit `callbacks` is required for a drain that happens *after* the turn that owned it
        has already ended -- `_finish_turn()`'s own end-of-turn drain, since `_dispatch_turn`
        clears `_current_turn_handlers` before `send_turn()` returns to any such caller."""
        messages = list(self._queued_messages)
        self._queued_messages.clear()
        handlers = callbacks if callbacks is not None else self._current_turn_handlers
        if handlers is not None:
            for queued_msg in messages:
                if handlers.on_send_queued_message is not None:
                    handlers.on_send_queued_message(queued_msg)
        return messages

    def load_messages(self, messages: list[Message]) -> None:
        """Replace this session's conversation history with `messages` — e.g. restoring a
        previous interactive session's history from `klorb.workspace.session_store`. Intended
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
        previous interactive session's counts from `klorb.workspace.session_store`. Intended
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
        `<SystemInterjection subject="{subject}">` tag (see `wrap_system_interjection`) and
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
        docs/adrs/00081-standing-interjections-complement-one-shot-for-level-triggered-state.md.
        """
        self._standing_interjection_providers[subject] = provider

    def append_system_note(self, content: str) -> None:
        """Append `content` directly to this session's message history as a `role="user"`
        message, complete on arrival -- without going through `send_turn()`/`_dispatch_turn()`.
        For content that must land durably in this session's own persisted transcript but was
        never actually part of a live turn -- e.g. `klorb.agents.runtime.
        cascade_close_subagents`'s subagent-termination relay at shutdown, which mirrors the
        "degenerate user message" `docs/specs/subagents.md`'s "Communicating back to
        the parent" section describes for delivering a completed subagent's output when this
        session has no turn of its own in flight to attach it to."""
        self._messages.append(Message(
            content=content, role="user", num_tokens=estimate_tokens(content),
            processing_state="complete", timestamp=datetime.now(),
        ))

    def current_turn_handlers(self) -> TurnEventHandlers | None:
        """Return the `TurnEventHandlers` for the turn currently in flight on this session, or
        `None` if none is. A tool's `apply()` runs synchronously on the same thread as
        `_dispatch_turn` (see `klorb.tools.interruptible_tool.InterruptibleTool.
        _active_cancel_event`'s docstring), so this is always set when `CreateSubagent`/
        `MessageSubagent` read it -- they use it to route a subagent's own permission asks
        through the same interactive callback this session's own turn is already using, tagged
        with the subagent's address/role -- see docs/specs/subagents.md's
        "Permissions" section."""
        return self._current_turn_handlers

    def register_teardown(self, subject: str, teardown: Callable[[], None]) -> None:
        """Register `teardown` to be invoked once by `close()`, keyed by `subject`.
        Re-registering under the same `subject` overwrites the previous callback rather than
        accumulating a second one — the same idempotency contract as
        `register_standing_interjection`, for the same reason (a caller that constructs a fresh
        instance per call can safely re-register every time)."""
        self._teardown_callbacks[subject] = teardown

    def fire_session_start_hook(
        self, event: str, *, workspace_just_bootstrapped: bool = False,
    ) -> None:
        """Dispatch `onSessionStart` for this session -- a no-op unless it's a root session
        (`self.parent is None`; a subagent never fires its own `onSessionStart`/`onSessionEnd`)
        with a `ProcessConfig`. `event` is one of `"NewSession"`/`"ResumeSession"` -- the
        caller's job to determine, since only it knows whether this session was freshly
        constructed or restored from disk. Callers: headless and ACP (`klorb.cli.main()`,
        `klorb.server.klorb_agent.KlorbAcpAgent.new_session`/`load_session`) call this right
        after constructing the session, since workspace trust is already settled by then; the
        TUI calls it from `_resolve_workspace_trust()`
        (`klorb.tui.mixins.workspace_bootstrap.WorkspaceBootstrapMixin`) instead, once trust is
        settled there.
        """
        if self.parent is not None or self._process_config is None:
            return
        self._dispatch_lifecycle_hook(
            "onSessionStart", event=event, workspace_just_bootstrapped=workspace_just_bootstrapped)
        self._start_workspace_event_watchers()

    def _start_workspace_event_watchers(self) -> None:
        """Start this root session's `FileSystemModified` watcher and `Timer` scheduler, for
        whichever of the two have `events` entries configured, and register their teardowns so
        `close()` stops them -- called once `onSessionStart` fires (see
        `fire_session_start_hook`), so trust is already settled and
        `self._process_config.events` reflects whatever config layer the resolved workspace can
        see."""
        assert self._process_config is not None
        fs_entries = cast(
            "list[FileSystemModifiedEventConfig]",
            self._process_config.events.get("FileSystemModified", []))
        if fs_entries:
            from klorb.hooks.fs_events import FileSystemWatcher
            watcher = FileSystemWatcher(
                self.config.workspace.path, fs_entries, dispatch=self._dispatch_fs_modified_event)
            watcher.start()
            self.register_teardown("FileSystemWatcher", watcher.close)
        timer_entries = cast("list[TimerEventConfig]", self._process_config.events.get("Timer", []))
        if timer_entries:
            from klorb.hooks.timer_events import TimerScheduler
            scheduler = TimerScheduler(
                self.config.workspace.path, timer_entries, dispatch=self._dispatch_timer_event)
            scheduler.start()
            self.register_teardown("TimerScheduler", scheduler.close)

    def _dispatch_fs_modified_event(
        self, entries: "list[FileSystemModifiedEventConfig]", event_input: "EventInput",
    ) -> None:
        """`FileSystemWatcher`'s dispatch callback: runs `entries`' actions as one chain and
        delivers any resulting `message` into this session's conversation. Called from the
        watcher's own debounce-timer thread, not this session's turn-dispatch thread -- see
        `Session.deliver_event_message`."""
        self._dispatch_event_entries("FileSystemModified", entries, event_input)

    def _dispatch_timer_event(
        self, entries: "list[TimerEventConfig]", event_input: "EventInput",
    ) -> None:
        """`TimerScheduler`'s dispatch callback -- one entry at a time, since each fires on its
        own independent schedule (see `TimerScheduler`). Called from that entry's own timer
        thread, not this session's turn-dispatch thread -- see `Session.deliver_event_message`."""
        self._dispatch_event_entries("Timer", entries, event_input)

    def _dispatch_event_entries(
        self, event_name: str, entries: "Sequence[EventConfig]", event_input: "EventInput",
    ) -> None:
        """Shared body for `_dispatch_fs_modified_event`/`_dispatch_timer_event`: run `entries`'
        actions as one chain and deliver any resulting `message` into this session's
        conversation."""
        from klorb.hooks.dispatcher import HookDispatcher
        assert self._process_config is not None
        output = HookDispatcher(
            self._process_config, api_provider=self._provider, model_registry=self._model_registry,
        ).dispatch_event(event_name, entries, event_input, session_config=self.config)
        if output.message is not None:
            cast("Session", self).deliver_event_message(output.message)

    def fire_workspace_trust_changed_hook(self, event: str) -> None:
        """Dispatch `WorkspaceTrustChanged` for this root session -- called by the TUI's
        `>Trust workspace` command (`event="TrustCommand"`) or ACP's `_klorb/trustWorkspace`
        (`event="AcpTrustWorkspace"`) once `_apply_workspace_config` has already reloaded this
        session's config against the newly-trusted workspace. A no-op for a subagent or a
        session with no `ProcessConfig`, mirroring `fire_session_start_hook`'s own guard --
        both callers only ever hold a reference to the root session in practice."""
        if self.parent is not None or self._process_config is None:
            return
        entries = self._process_config.events.get("WorkspaceTrustChanged", [])
        if not entries:
            return
        from klorb.hooks.dispatcher import HookDispatcher
        from klorb.hooks.wire import EventInput
        output = HookDispatcher(
            self._process_config, api_provider=self._provider, model_registry=self._model_registry,
        ).dispatch_event(
            "WorkspaceTrustChanged", entries,
            EventInput(
                hook="WorkspaceTrustChanged", workspaceRoot=str(self.config.workspace.path),
                event=event, role=self.config.role_name, session_id=self.id),
            session_config=self.config)
        if output.message is not None:
            cast("Session", self).deliver_event_message(output.message)

    def _dispatch_lifecycle_hook(
        self, hook_name: str, *, event: str, workspace_just_bootstrapped: bool = False,
    ) -> "HookOutput":
        """Build a `HookInput` describing this session's `workspace`/`event` and dispatch
        `hook_name` -- `onSessionStart`/`onSessionEnd`'s own shape, on top of the shared
        `_dispatch_hook` every other lifecycle/turn/tool hook point uses."""
        return self._dispatch_hook(
            hook_name, event=event, workspaceTrusted=self.config.workspace.trusted,
            workspaceJustBootstrapped=workspace_just_bootstrapped)

    def _dispatch_hook(self, hook_name: str, **hook_input_kwargs: Any) -> "HookOutput":
        """Dispatch `hook_name` against this session's own `ProcessConfig`/`SessionConfig`,
        tagging the built `HookInput` with this session's `workspace`/`role`/`id` and passing
        through `hook_input_kwargs` (e.g. `event=`, `message=`, `toolName=`) -- the shared
        building block every hook-firing call site in `klorb.session.mixins` (turn dispatch,
        tool execution, `_dispatch_lifecycle_hook` above) and `klorb.agents.policy` (subagent
        lifecycle) goes through. Returns a default (`success=True`, no message) `HookOutput` if
        this session has no `ProcessConfig` -- hooks are inert for a `Session` constructed
        without one, e.g. most unit tests. Deferred imports: `klorb.hooks` doesn't depend on
        `klorb.session`, but importing it at module level here would still be the wrong
        direction for a mixin most callers construct without ever touching hooks."""
        from klorb.hooks.dispatcher import HookDispatcher
        from klorb.hooks.wire import HookInput, HookOutput
        if self._process_config is None:
            return HookOutput()
        return HookDispatcher(
            self._process_config, api_provider=self._provider, model_registry=self._model_registry,
        ).dispatch(
            hook_name,
            HookInput(
                hook=hook_name, workspaceRoot=str(self.config.workspace.path),
                role=self.config.role_name, session_id=self.id, **hook_input_kwargs),
            session_config=self.config)

    def fire_subagent_start_hook(self, message: str) -> str | None:
        """Dispatch `onSubagentStart` for this subagent session, about to run its first (or
        resumed) turn -- called by `klorb.agents.policy._run_subagent_turn`, mirroring
        `onSubmitUserPrompt`'s message-rewrite/veto contract for the root session's own turns
        (see `SessionTurnsMixin._dispatch_turn`). Returns the message to actually send: `message`
        itself, unless the aggregate `HookOutput` set `message` (a rewrite) or `success=False`
        (a veto, signaled by returning `None` -- the caller must not start the turn)."""
        result = self._dispatch_hook("onSubagentStart", message=message)
        if result.success is False:
            return None
        return result.message if result.message is not None else message

    def fire_subagent_turn_end_hook(self, output: str) -> None:
        """Dispatch `onSubagentTurnEnd` for this subagent session once its turn ends -- called by
        `klorb.agents.policy._run_subagent_turn`, mirroring `onAgentTurnEnd`'s `chat`-handler
        chaining for the root session's own turns (see `SessionTurnsMixin._dispatch_turn`): a
        `message` in the aggregate `HookOutput` starts (or queues) a follow-up turn on this same
        subagent session via `start_turn_or_enqueue`, not on the parent that created it."""
        result = self._dispatch_hook("onSubagentTurnEnd", message=output)
        if result.message is not None:
            cast("Session", self).start_turn_or_enqueue(result.message)

    def close(self) -> None:
        """Persist a final `session.json` and release `session.lock` (see
        `SessionPersistenceMixin._finalize_session_persistence`, a no-op if this session never
        claimed a directory), then tear down any live per-session resources registered via
        `register_teardown` -- `BashTool`'s persistent shell, if one is alive, and
        `self.scratchpad`'s cleanup (see `klorb.tools.scratchpad.common.Scratchpad.cleanup`,
        registered in `__init__`). Idempotent: calling this when nothing is registered, or
        calling it twice, is a no-op the second time onward since each teardown callback (e.g.
        `PersistentShell.kill`, `Scratchpad.cleanup`) is itself safe to call more than once.

        Callers that replace a `Session` outright (`klorb.tui.ReplApp.clear_session()`) must
        call this on the outgoing `Session` first — nothing else tears it down, and a live
        `subprocess.Popen` handle would otherwise leak for the rest of the klorb process's
        lifetime. See docs/plans/archive/005-session-scoped-bash-terminals.md.

        Before any of that, dispatches `onSessionEnd` (root session only -- see
        `fire_session_start_hook`) with `event="SuspendSession"`, then cascade-closes every
        subagent this session has directly or indirectly created (see
        `klorb.agents.runtime.cascade_close_subagents`), relaying each one's not-yet-delivered
        output (or a termination note, for one still running) into this session's own
        `messages` first -- so `_finalize_session_persistence()` below captures it in
        `session.json`, per docs/specs/subagents.md's "Persistence" section.

        A `clear_session` result from `onSessionEnd` (guaranteed by `HookDispatcher` to carry a
        non-empty `message`) is handed to `on_clear_session_requested` once every other step
        above has finished -- discarding this session and starting a fresh one is exactly what
        this method is already about to do the rest of, so the request is honored after, not
        instead of, the teardown work. Logged at `warning` and otherwise dropped if no host has
        registered that callback.
        """
        clear_session_message: str | None = None
        if self.parent is None and self._process_config is not None:
            result = self._dispatch_lifecycle_hook("onSessionEnd", event="SuspendSession")
            if result.clear_session:
                assert result.message is not None
                clear_session_message = result.message
        from klorb.agents.runtime import cascade_close_subagents
        cascade_close_subagents(cast("Session", self))
        self._finalize_session_persistence()
        for teardown in self._teardown_callbacks.values():
            teardown()
        self._teardown_callbacks.clear()
        if clear_session_message is not None:
            if self.on_clear_session_requested is not None:
                self.on_clear_session_requested(clear_session_message)
            else:
                logger.warning(
                    "onSessionEnd hook requested clear_session but this session has no "
                    "on_clear_session_requested handler registered; ignoring.")

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
