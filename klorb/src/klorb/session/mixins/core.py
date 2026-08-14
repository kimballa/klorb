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
    from klorb.hooks.hook_api import EventInput, HookOutput
    # isort: on

logger = logging.getLogger(__name__)

_FILE_SYSTEM_WATCHER_TEARDOWN_SUBJECT = "FileSystemWatcher"
_TIMER_SCHEDULER_TEARDOWN_SUBJECT = "TimerScheduler"
_INFRASTRUCTURE_TEARDOWN_SUBJECTS = frozenset({
    _FILE_SYSTEM_WATCHER_TEARDOWN_SUBJECT, _TIMER_SCHEDULER_TEARDOWN_SUBJECT})
"""Teardown subjects `_reset_state()` must never touch: workspace/process-level resources
registered once at root-session start (`_start_workspace_event_watchers`) and torn down only by
a real `close()`, never recreated mid-session -- unlike `Scratchpad`/`Bash`'s persistent shell,
which `_reset_state()` does tear down and let their owners lazily recreate."""

_SESSION_NAMING_TEARDOWN_SUBJECT = "SessionNaming"
"""Teardown subject for `_start_session_naming`'s token-invalidation callback."""


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
        self._session_naming_pending = self._session_name is None
        """Whether `send_turn()` still needs to kick off the one-shot session-naming classifier
        (see `_start_session_naming`) on its first call. Seeded `False` when a `session_name` was
        already supplied to this constructor (a restored session that was already named), `True`
        otherwise (a fresh session, or a restored session that was never successfully named).
        Set unconditionally the first time `send_turn()` checks it, regardless of the
        classifier's eventual outcome, so a failed/timed-out attempt is never retried."""
        self._session_naming_token: object | None = None
        """Set to a fresh sentinel object by `_start_session_naming` right before it spawns the
        classifier's background thread; that thread only applies its result (and persists it) if
        this is still the same object when it finishes."""
        self._last_modified_at = last_modified_at
        """When this session was last saved to `session.json`, or `None` if it hasn't been saved
        yet (or was restored from a save file that predates this field). Restored from
        `SessionState.last_modified_timestamp` if given; otherwise stamped with `datetime.now()`
        by `SessionPersistenceMixin._write_session_state_and_touch()` on every save, and
        round-trips through `session.json`/`sessions.json` (`RecentSession.
        last_modified_timestamp`) like `id`/`session_name`."""
        self._teardown_callbacks: dict[str, Callable[[], None]] = {}
        """Callbacks registered via `register_teardown()`, keyed by subject, invoked once each
        by `close()` -- except a conversation-scoped one (e.g. `Scratchpad`, `Bash`'s persistent
        shell), which `_reset_state()` also invokes and re-registers on a `reset_session()` call.
        See `register_teardown`/`close`/`_reset_state`."""
        self._notice_handler: Callable[[str], None] | None = None
        """The callback `register_notice_handler()` sets, used by `deliver_notice()` to surface a
        hook's `HookOutput.log` to whichever UI is attached to this session. Identity-scoped like
        `_teardown_callbacks`, not reset by `reset_session()`."""
        self._wake_handler: Callable[[], None] | None = None
        """The callback `register_wake_handler()` sets, used by `deliver_wake()` to tell an idle
        host "something just got queued, come drain it" (see `SessionTurnsMixin.
        deliver_event_message`)."""
        self._reset_state(scratchpad_path=scratchpad_path)
        self._session_lock: Lockfile | None = None
        """The `session.lock` held on this session's `sessions/<subdir>/` directory, or `None`
        if this session hasn't claimed one yet (or ever will -- an untrusted workspace never
        claims one). Set by `SessionPersistenceMixin.claim_session_directory`, released and
        cleared by `SessionPersistenceMixin._finalize_session_persistence` (called from
        `close()`)."""
        self._session_subdir: str | None = None
        """The `sessions/<subdir>/` name this session's state is saved under, or `None` before
        `claim_session_directory` has run."""
        self._session_claimed = False
        """Whether this session currently owns a `sessions/<subdir>/` directory (holds its
        `session.lock`) -- see `SessionPersistenceMixin.claim_session_directory`."""

    def _reset_state(self, *, scratchpad_path: str | None = None) -> None:
        """Reset every conversation-scoped field to a freshly constructed `Session`'s own value.
        Called once by `__init__` itself, and again by `reset_session()` to wipe an existing
        session's conversation in place (`HookOutput.reset_session`'s effect -- see
        docs/specs/hooks-and-events.md). Deliberately leaves untouched: identity (`id`/`root_id`/
        `parent`/`depth`), anything derived from `config`/`process_config` alone
        (`_role`, `_system_prompt`, `_mention_read_file_core`, ...) -- `reset_session()` handles
        `config` and its derivatives itself, since unlike this method it must reinitialize them
        from `ProcessConfig.session`'s template rather than leave them alone -- and persistence
        identity (`_session_lock`/`_session_subdir`/`_session_claimed`, `_last_modified_at`):
        a reset keeps using the same on-disk directory, it doesn't claim a new one.

        `_teardown_callbacks` is similarly left alone as a dict (never reassigned) since a
        root session's `FileSystemModified`/`Timer` watchers are registered once, at session
        start, and nothing recreates them mid-session -- only a conversation-scoped teardown
        (`Scratchpad`, `Bash`'s persistent shell) is actually invoked and dropped here, so a
        live shell/scratchpad from before the reset doesn't leak past it. `scratchpad_path`
        threads through to a fresh `Scratchpad`: `__init__` passes its own constructor arg
        (a caller-supplied path to reuse, or `None` for a freshly provisioned one);
        `reset_session()` always passes `None`, since a reset has no path to reuse.
        """
        self.cur_chainlink_task_id: int | None = None
        self.tool_state: dict[str, Any] = {}
        self.active_cancel_event = None
        self._tool_calls_this_turn = 0
        self._skill_catalog_registry = SkillCatalogRegistry()
        self._messages: list[Message] = []
        self._skills_seeded = False
        self._context_files_seeded = False
        self._memories_seeded = False
        self._metadata_seeded = False
        self._session_started_at = datetime.now()
        self._pending_permission_framework_interjection: str | None = None
        self._standing_interjection_providers: dict[str, Callable[[], str | None]] = {}
        self._queued_messages: list[QueuedMessage] = []
        self._user_msg_event: threading.Event = threading.Event()
        self._current_turn_handlers: TurnEventHandlers | None = None
        self._current_turn_mentioned_skill_ids: frozenset[tuple[str, str]] = frozenset()
        self._current_turn_leading_skill_id: tuple[str, str] | None = None
        self._chained_hook_turns = 0
        self._chain_continuation_pending = False
        self.subagent_tracker = self._create_subagent_tracker()
        self._register_agent_group_standing_interjection()
        self.statistics = SessionStatistics()
        for subject, teardown in list(self._teardown_callbacks.items()):
            if subject in _INFRASTRUCTURE_TEARDOWN_SUBJECTS:
                continue
            logger.debug("Tearing down conversation-scoped resource %r for reset.", subject)
            teardown()
            del self._teardown_callbacks[subject]
        self.scratchpad = Scratchpad(scratchpad_path)
        self.register_teardown("Scratchpad", self.scratchpad.cleanup)

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

    def cancel_session_naming(self) -> None:
        """Invalidate any in-flight background session-naming classifier call so its eventual
        result can no longer overwrite `name`."""
        self._session_naming_token = None

    def _start_session_naming(
        self, prompt_text: str, callbacks: "TurnEventHandlers | None",
    ) -> None:
        """Kick off the session-naming classifier on a background daemon thread and return
        immediately, so the first turn's own dispatch never waits on its round trip."""
        token = object()
        self._session_naming_token = token

        def run() -> None:
            result = self._run_session_naming(prompt_text)
            if self._session_naming_token is not token:
                return
            try:
                self.name = result.title if result is not None else fallback_session_title(prompt_text)
                if callbacks is not None and callbacks.on_session_name_changed is not None:
                    callbacks.on_session_name_changed(result)
                self.persist_state()
            except Exception:
                # A classifier reply landing in the exact instant `close()`/`reset_session()`
                # tears this session down is a rare, inherently racy edge case the token check
                # above doesn't fully close -- caught here so it surfaces as a log line, not a
                # bare, easy-to-miss daemon-thread traceback, mirroring `FileSystemWatcher.
                # _flush`'s own dispatch-failure handling.
                logger.warning("Session naming's post-classifier apply failed.", exc_info=True)

        thread = threading.Thread(target=run, name="session-naming", daemon=True)

        def _teardown() -> None:
            # Give a call that's about to finish on its own a bounded chance to land its result
            # before cutting it off -- this matters most for a headless one-shot run, whose
            # process exits right after `close()`: without this join, an async classifier would
            # almost always lose that race and the session would never get a real title. Bounded
            # by the classifier's own end-to-end deadline, so this can't outlast a call that was
            # already going to time out on its own.
            thread.join(timeout=self._session_naming_e2e_timeout())
            self._session_naming_token = None

        self.register_teardown(_SESSION_NAMING_TEARDOWN_SUBJECT, _teardown)
        thread.start()

    def _session_naming_e2e_timeout(self) -> float:
        """`ProcessConfig.session_classifier_e2e_timeout_seconds`, or
        `DEFAULT_SESSION_CLASSIFIER_E2E_TIMEOUT_SECONDS` when this session has no
        `ProcessConfig`."""
        from klorb.process_config import DEFAULT_SESSION_CLASSIFIER_E2E_TIMEOUT_SECONDS
        return (
            self._process_config.session_classifier_e2e_timeout_seconds
            if self._process_config is not None
            else DEFAULT_SESSION_CLASSIFIER_E2E_TIMEOUT_SECONDS
        )

    def _run_session_naming(self, prompt_text: str) -> "SessionName | None":
        """Derive a `SessionName` (a title) from `prompt_text` (this session's first submitted
        prompt) via `klorb.session_naming.generate_session_name`."""
        # Deferred: `klorb.process_config` imports from `klorb.session`, so a module-level
        # import here would be circular.
        from klorb.process_config import DEFAULT_SESSION_CLASSIFIER_TIMEOUT_SECONDS

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
        e2e_timeout = self._session_naming_e2e_timeout()
        reasoning = thinking_effort_for(cast("Session", self), model)
        return generate_session_name(
            prompt_text, api_provider=self._provider, model=model, timeout=timeout,
            e2e_timeout=e2e_timeout, reasoning=reasoning)

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

    def _register_agent_group_standing_interjection(self) -> None:
        """Register the `AgentGroup` standing interjection provider, which emits a markdown
        table of every agent in the session tree whenever group composition or subagent activity
        changes. Deferred import: `klorb.agents.runtime` itself imports
        `klorb.session.mixins.turns` at module level, so a module-level import here would be
        circular."""
        from klorb.agents.runtime import (
            AGENT_GROUP_INTERJECTION_SUBJECT,
            build_agent_group_interjection_provider,
        )
        provider = build_agent_group_interjection_provider(cast("Session", self))
        self.register_standing_interjection(AGENT_GROUP_INTERJECTION_SUBJECT, provider)

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

    def mark_next_turn_continuation(self, drained: list[QueuedMessage]) -> None:
        """Mark whether the upcoming `_dispatch_turn()` should be treated as a pure hook-chain
        continuation -- leaving `_chained_hook_turns` alone instead of resetting it -- based on
        `drained`'s origins: `True` only when every message came from a chat handler's own
        chaining (`origin == "chained_hook"`). A real user or event message mixed into the
        batch means something other than pure auto-chaining is driving the next turn, so the
        counter resets like any ordinary turn (see `_dispatch_turn`). Exposed separately from
        `drain_next_turn_text()` for a caller that needs `drain_queued_messages()`'s raw list
        for its own purposes (ACP's `TurnBridge.run_turn`, which sends a per-message
        notification `drain_next_turn_text()` has no way to)."""
        self._chain_continuation_pending = bool(drained) and all(
            queued_msg.origin == "chained_hook" for queued_msg in drained)

    def drain_next_turn_text(self, callbacks: TurnEventHandlers | None = None) -> str | None:
        """Drain every queued message (see `drain_queued_messages`) and join them into the text
        for one new turn, or `None` if nothing was queued -- the shared "resubmit whatever's
        queued through the front door" step every host's own turn loop ends with (TUI's
        `_finish_turn`, `Session.run_one_shot`, `klorb.agents.policy._run_subagent_turn`)."""
        drained = self.drain_queued_messages(callbacks)
        if not drained:
            return None
        self.mark_next_turn_continuation(drained)
        return "\n\n".join(queued_msg.message_text for queued_msg in drained)

    def _deliver_chained_hook_message(self, message: str) -> None:
        """Enqueue `message` (an `onAgentTurnEnd`/`onSubagentTurnEnd` `chat` handler's result)
        as this session's next turn, to be picked up by whichever drain-and-resubmit loop is
        already driving this session (see `drain_next_turn_text`) once the current turn ends --
        rather than dispatching it here, which would run with no live UI callbacks. A negative
        `config.max_chained_hook_turns` disables the cap."""
        max_turns = self.config.max_chained_hook_turns
        if max_turns >= 0 and self._chained_hook_turns >= max_turns:
            logger.warning(
                "Chained hook turn cap (%d) reached; refusing to chain another turn.", max_turns)
            return
        self._chained_hook_turns += 1
        self.enqueue_queued_message(QueuedMessage(message_text=message, origin="chained_hook"))

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

    def register_notice_handler(self, handler: Callable[[str], None]) -> None:
        """Register `handler` as this session's sink for `HookOutput.log` (see
        `deliver_notice`) -- only one is supported at a time; a later call replaces the
        previous one, e.g. `klorb.tui.ReplApp` re-registering after replacing its own
        `_session`."""
        self._notice_handler = handler

    def deliver_notice(self, text: str) -> None:
        """Surface `text` (a hook's `HookOutput.log`) to this session's registered notice
        handler, if any -- a no-op otherwise, e.g. a `Session` built for a unit test. Unlike
        `deliver_event_message`, never touches the conversation: `text` is meant to be read, not
        sent to the model."""
        if self._notice_handler is not None:
            self._notice_handler(text)

    def register_wake_handler(self, handler: Callable[[], None]) -> None:
        """Register `handler` as this session's "come drain the queue" ping (see
        `deliver_wake`)."""
        self._wake_handler = handler

    def deliver_wake(self) -> None:
        """Tell this session's registered wake handler, if any, that a message was just queued
        while no turn was in flight."""
        if self._wake_handler is not None:
            self._wake_handler()

    def fire_session_start_hook(
        self, reason: str, *, workspace_just_bootstrapped: bool = False,
    ) -> None:
        """Dispatch `onSessionStart` for this session -- a no-op unless it's a root session
        (`self.parent is None`; a subagent never fires its own `onSessionStart`/`onSessionEnd`)
        with a `ProcessConfig`. `reason` is one of `"NewSession"`/`"ResumeSession"` -- the
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
            "onSessionStart", reason=reason, workspace_just_bootstrapped=workspace_just_bootstrapped,
            include_config=True)
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
        actions as one chain and deliver any resulting `message`/`reset_session` into this
        session's conversation (see `_deliver_or_reset_event`)."""
        from klorb.hooks.dispatcher import HookDispatcher
        assert self._process_config is not None
        event_input.session_id = self.id
        event_input.root_session_id = self.root_id
        event_input.is_agent_active = self.current_turn_handlers() is not None
        output = HookDispatcher(
            self._process_config, api_provider=self._provider, model_registry=self._model_registry,
        ).dispatch_event(event_name, entries, event_input, session_config=self.config)
        self._deliver_or_reset_event(output)

    def _deliver_or_reset_event(self, output: "HookOutput") -> None:
        """Shared tail for `_dispatch_event_entries`/`fire_workspace_trust_changed_hook`: surface
        `output.log`, then either reset this session in place and deliver `output.message` as
        the reset conversation's first turn, or deliver it as an ordinary event message."""
        if output.log is not None:
            self.deliver_notice(output.log)
        if output.reset_session:
            assert output.message is not None
            self._dispatch_lifecycle_hook("onSessionEnd", reason="ResetSession")
            cast("Session", self).reset_session()
            cast("Session", self).deliver_event_message(output.message)
            return
        if output.message is not None:
            cast("Session", self).deliver_event_message(output.message)

    def fire_workspace_trust_changed_hook(self, reason: str) -> None:
        """Dispatch `WorkspaceTrustChanged` for this root session -- called by the TUI's
        `>Trust workspace` command (`reason="TrustCommand"`) or ACP's `_klorb/trustWorkspace`
        (`reason="AcpTrustWorkspace"`) once `_apply_workspace_config` has already reloaded this
        session's config against the newly-trusted workspace. A no-op for a subagent or a
        session with no `ProcessConfig`, mirroring `fire_session_start_hook`'s own guard --
        both callers only ever hold a reference to the root session in practice."""
        if self.parent is not None or self._process_config is None:
            return
        entries = self._process_config.events.get("WorkspaceTrustChanged", [])
        if not entries:
            return
        from klorb.hooks.dispatcher import HookDispatcher
        from klorb.hooks.hook_api import EventInput
        output = HookDispatcher(
            self._process_config, api_provider=self._provider, model_registry=self._model_registry,
        ).dispatch_event(
            "WorkspaceTrustChanged", entries,
            EventInput(
                hook="WorkspaceTrustChanged", workspace_root=str(self.config.workspace.path),
                reason=reason, role=self.config.role_name, session_id=self.id,
                root_session_id=self.root_id,
                is_agent_active=self.current_turn_handlers() is not None),
            session_config=self.config)
        self._deliver_or_reset_event(output)

    def _dispatch_lifecycle_hook(
        self, hook_name: str, *, reason: str, workspace_just_bootstrapped: bool = False,
        include_config: bool = False,
    ) -> "HookOutput":
        """Build a `HookInput` describing this session's `workspace`/`reason` and dispatch
        `hook_name` -- `onSessionStart`/`onSessionEnd`'s own shape, on top of the shared
        `_dispatch_hook` every other lifecycle/turn/tool hook point uses. `include_config` attaches
        the entire resolved `ProcessConfig` to the built `HookInput`, for `onSessionStart` only."""
        assert self._process_config is not None
        config = self._process_config.model_dump(mode="json") if include_config else None
        return self._dispatch_hook(
            hook_name, reason=reason, workspace_trusted=self.config.workspace.trusted,
            workspace_just_bootstrapped=workspace_just_bootstrapped, config=config)

    def _dispatch_hook(self, hook_name: str, **hook_input_kwargs: Any) -> "HookOutput":
        """Dispatch `hook_name` against this session's own `ProcessConfig`/`SessionConfig`,
        tagging the built `HookInput` with this session's `workspace`/`role`/`id` and passing
        through `hook_input_kwargs` (e.g. `reason=`, `message=`, `tool_name=`) -- the shared
        building block every hook-firing call site in `klorb.session.mixins` (turn dispatch,
        tool execution, `_dispatch_lifecycle_hook` above) and `klorb.agents.policy` (subagent
        lifecycle) goes through. Returns a default (`success=True`, no message) `HookOutput` if
        this session has no `ProcessConfig` -- hooks are inert for a `Session` constructed
        without one, e.g. most unit tests. Deferred imports: `klorb.hooks` doesn't depend on
        `klorb.session`, but importing it at module level here would still be the wrong
        direction for a mixin most callers construct without ever touching hooks."""
        from klorb.hooks.dispatcher import HookDispatcher
        from klorb.hooks.hook_api import HookInput, HookOutput
        if self._process_config is None:
            return HookOutput()
        output = HookDispatcher(
            self._process_config, api_provider=self._provider, model_registry=self._model_registry,
        ).dispatch(
            hook_name,
            HookInput(
                hook=hook_name, workspace_root=str(self.config.workspace.path),
                role=self.config.role_name, session_id=self.id, root_session_id=self.root_id,
                **hook_input_kwargs),
            session_config=self.config)
        if output.log is not None:
            self.deliver_notice(output.log)
        return output

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
        `message` in the aggregate `HookOutput` is delivered via `_deliver_chained_hook_message`
        as a follow-up turn on this same subagent session, not on the parent that created it."""
        result = self._dispatch_hook("onSubagentTurnEnd", message=output)
        if result.message is not None:
            self._deliver_chained_hook_message(result.message)

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
        `fire_session_start_hook`) with `reason="SuspendSession"`.

        Cascade-closes every subagent this session has directly or indirectly created (see
        `klorb.agents.runtime.cascade_close_subagents`), relaying each one's not-yet-delivered
        output (or a termination note, for one still running) into this session's own `messages`
        first -- so `_finalize_session_persistence()` below captures it in `session.json`, per
        docs/specs/subagents.md's "Persistence" section.
        """
        if self.parent is None and self._process_config is not None:
            self._dispatch_lifecycle_hook("onSessionEnd", reason="SuspendSession")
        from klorb.agents.runtime import cascade_close_subagents
        cascade_close_subagents(cast("Session", self))
        self._finalize_session_persistence()
        for teardown in self._teardown_callbacks.values():
            teardown()
        self._teardown_callbacks.clear()

    def reset_session(self) -> None:
        """Wipe this session's conversation and start it over in place, as if it were a freshly
        constructed `Session` reusing the same `id`/on-disk directory -- `HookOutput.
        reset_session`'s effect (see docs/specs/hooks-and-events.md's "Session reset" section).
        Called by `close()` (an `onSessionEnd` result) and `SessionTurnsMixin.
        _fire_agent_turn_end_hook` (an `onAgentTurnEnd` result).

        Cascade-closes any live subagents first (their relayed output lands in `self._messages`
        only to be wiped a moment later by `_reset_state()`, same as a real `close()` would
        capture it first). Reinitializes `config` from `ProcessConfig.session`'s template (a
        no-op without a `ProcessConfig`, e.g. most unit tests) -- unlike `_reset_state()`, which
        leaves `config` and everything derived from it alone, since `__init__`'s own call to
        `_reset_state()` must not clobber a caller-supplied `config` (a restored session, a
        subagent's inherited one).

        Does not deliver the reset's continuation message itself -- the two callers have
        different hosts (or none) available to deliver it to, so each does that separately
        after calling this."""
        from klorb.agents.runtime import cascade_close_subagents
        cascade_close_subagents(cast("Session", self))
        if self._process_config is not None:
            self.config = self._process_config.session.model_copy()
            if self.parent is None:
                self.config.permission_framework_state = PermissionFrameworkState(
                    value=self.config.permission_framework_state.value)
            self._role = get_role(self.config.role_name)
            self._system_prompt = SystemPrompt(
                self.config, self._role, self._model_registry, self._process_config)
        self._reset_state()
        self._session_name = None
        self._session_naming_pending = True
        logger.info("Session %s reset in place via a hook's reset_session request.", self.id)

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
