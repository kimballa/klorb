# © Copyright 2026 Aaron Kimball
"""`SessionBase`: attribute-only declarations shared by every `Session` mixin.

`Session` itself (`klorb.session.__init__`) is composed from several mixins, each holding one
cohesive slice of its methods, verbatim -- `self.foo(...)` resolves at runtime via MRO
regardless of which mixin `foo` physically lives in, so no call site needs to change. But
this repo's `make typecheck` runs mypy with `--disallow-untyped-calls` and
`--disallow-untyped-globals`, so a mixin method referencing `self._some_attr` (set by
`SessionCoreMixin.__init__`, which lives in a different file) needs `_some_attr` visible on
`self`'s declared type from that mixin's own point of view -- mypy doesn't know that whichever
concrete class eventually mixes this one in will also mix in the one that sets it.
`SessionBase` is that shared point of view: every mixin declares `class FooMixin(SessionBase):`,
and `Session(FooMixin, ..., SessionBase)` is the only class with a real `__init__` body. This
class carries no behavior of its own. See `klorb.tui._base.ReplAppBase` for the precedent this
mirrors.
"""

import threading
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from klorb.api_provider import ApiProvider
from klorb.images.prepare import ImagePipelineConfig
from klorb.lockfile import Lockfile
from klorb.message import Message, ToolCallRequest
from klorb.models.model import CacheMgmtStyle, Model
from klorb.models.registry import ModelRegistry
from klorb.permissions.table import MultiPermissionAskRequired, PermissionAskRequired
from klorb.role import Role
from klorb.session.config import SessionConfig
from klorb.session.constants import ThinkingEffort
from klorb.session.events import (
    PermissionDecision,
    QueuedMessage,
    ToolCallOutcome,
    TurnEventHandlers,
    UserSkillActivation,
)
from klorb.session_naming import SessionName
from klorb.session_statistics import SessionStatistics
from klorb.system_prompt import SystemPrompt
from klorb.tools.ask.common import AskUserQuestionsRequired
from klorb.tools.escalate_privileges.common import EscalatePrivilegesRequired
from klorb.tools.scratchpad.common import Scratchpad
from klorb.tools.skill.catalog import SkillCatalogRegistry
from klorb.tools.skill.model import Skill

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
    # so importing it for real here would be circular; needed only to type `parent` below,
    # since a subagent's `parent` is another `Session`.
    from klorb.session import Session
    # `klorb.agents.runtime` imports `klorb.session.mixins.turns` (for `wrap_system_interjection`),
    # which itself is part of assembling `Session` -- a real import here would be circular.
    from klorb.agents.runtime import SubagentTracker
    # `klorb.hooks.dispatcher` (which `_dispatch_hook` imports for real, deferred, where it's
    # actually used) depends on `klorb.session.config`, so a real import here would be circular;
    # needed only to type `_dispatch_hook`'s return value.
    from klorb.hooks.hook_api import HookOutput
    # isort: on


class SessionBase:
    """Attribute and cross-mixin method declarations for every field/method `Session` and its
    mixins reference on `self` from outside the file that actually defines it, so each mixin
    file type-checks on its own despite referencing state or behavior a different mixin (or
    `Session` itself) sets up. See the module docstring for why this class exists. Method
    stubs here are never called -- every one is overridden by the mixin that actually owns it
    once mixed into the concrete `Session`.
    """

    config: SessionConfig
    id: str
    root_id: str
    depth: int
    parent: "Session | None"
    effective_subagent_roles: frozenset[str]
    _max_output_tokens: int | None
    _next_child_index: int
    _child_index: int
    cur_chainlink_task_id: int | None
    _session_name: str | None
    _role: Role
    _provider: ApiProvider
    _model_registry: ModelRegistry
    _system_prompt: SystemPrompt
    _process_config: "ProcessConfig | None"
    _mention_read_file_core: "ReadFileCore"
    _image_pipeline_config: ImagePipelineConfig
    _thinking_token_budgets: dict[ThinkingEffort, int]
    _tool_registry: "ToolRegistry | None"
    tool_state: dict[str, Any]
    active_cancel_event: threading.Event | None
    _tool_calls_this_turn: int
    _compatibility_claude_markdown: bool
    _compatibility_claude_skills: bool
    _skill_catalog_registry: SkillCatalogRegistry
    _log_tool_calls: bool
    _messages: list[Message]
    _skills_seeded: bool
    _context_files_seeded: bool
    _memories_seeded: bool
    _metadata_seeded: bool
    _session_naming_pending: bool
    _session_naming_token: object | None
    _session_started_at: datetime
    _last_modified_at: datetime | None
    _pending_permission_framework_interjection: str | None
    _standing_interjection_providers: dict[str, Callable[[], str | None]]
    _teardown_callbacks: dict[str, Callable[[], None]]
    _queued_messages: list[QueuedMessage]
    _user_msg_event: threading.Event
    _current_turn_handlers: TurnEventHandlers | None
    _current_turn_mentioned_skill_ids: frozenset[tuple[str, str]]
    _current_turn_leading_skill_id: tuple[str, str] | None
    _chained_hook_turns: int
    _chain_continuation_pending: bool
    scratchpad: Scratchpad
    subagent_tracker: "SubagentTracker"
    statistics: SessionStatistics
    _session_lock: Lockfile | None
    _session_subdir: str | None
    _session_claimed: bool
    _wake_handler: Callable[[], None] | None

    def close(self) -> None: ...

    def deliver_wake(self) -> None: ...

    def reset_session(self) -> None: ...

    def claim_session_directory(self) -> None: ...

    def adopt_claimed_session_directory(self, subdir: str, lock: Lockfile) -> None: ...

    def persist_state(self) -> None: ...

    def _finalize_session_persistence(self) -> None: ...

    def active_model(self) -> Model | None:
        raise NotImplementedError

    def active_model_name(self) -> str:
        raise NotImplementedError

    def _drop_reasoning(self) -> bool:
        raise NotImplementedError

    def _cache_mgmt_style(self) -> CacheMgmtStyle:
        raise NotImplementedError

    def _resolve_system_prompt(self) -> str:
        raise NotImplementedError

    def _reasoning_params(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def _ensure_system_message(self) -> None: ...

    def _ensure_tool_defs_message(self) -> None: ...

    def _run_session_naming(self, prompt_text: str) -> "SessionName | None":
        raise NotImplementedError

    def _start_session_naming(
        self, prompt_text: str, callbacks: "TurnEventHandlers | None",
    ) -> None: ...

    def cancel_session_naming(self) -> None: ...

    def _ensure_skill_catalog(self) -> None: ...

    def discover_skills(self) -> list[Skill]:
        raise NotImplementedError

    def _build_context_files_interjection(self) -> str | None:
        raise NotImplementedError

    def _build_available_skills_interjection(self, skills: list[Skill]) -> str | None:
        raise NotImplementedError

    def _build_memories_interjection(self) -> str | None:
        raise NotImplementedError

    def _build_skill_reference_interjection(
        self, tokens: list[str], *, exclude: frozenset[tuple[str, str]] = frozenset(),
    ) -> str | None:
        raise NotImplementedError

    def _build_user_skill_activation_interjection(self, skill: Skill) -> UserSkillActivation | None:
        raise NotImplementedError

    def _confirm_limit_increase(
        self,
        current_count: int,
        current_limit: int,
        on_tool_call_limit_reached: Callable[[str], bool] | None,
    ) -> bool:
        raise NotImplementedError

    def _retry_after_permission_decision(
        self,
        call: ToolCallRequest,
        args: dict[str, Any],
        ask_exc: PermissionAskRequired,
        decision: PermissionDecision,
    ) -> ToolCallOutcome:
        raise NotImplementedError

    def _resolve_multi_permission_ask(
        self,
        call: ToolCallRequest,
        args: dict[str, Any],
        multi_ask_exc: MultiPermissionAskRequired,
        callbacks: TurnEventHandlers,
    ) -> ToolCallOutcome:
        raise NotImplementedError

    def _resolve_ask_user_questions(
        self,
        call: ToolCallRequest,
        ask_exc: AskUserQuestionsRequired,
        callbacks: TurnEventHandlers,
    ) -> ToolCallOutcome:
        raise NotImplementedError

    def _resolve_escalate_privileges(
        self,
        call: ToolCallRequest,
        escalate_exc: EscalatePrivilegesRequired,
        callbacks: TurnEventHandlers,
    ) -> ToolCallOutcome:
        raise NotImplementedError

    def _run_tool_calls(
        self,
        tool_use_message: Message,
        callbacks: TurnEventHandlers,
    ) -> None: ...

    def append_system_note(self, content: str) -> None: ...

    def current_turn_handlers(self) -> TurnEventHandlers | None:
        raise NotImplementedError

    def enqueue_queued_message(self, queued_msg: QueuedMessage) -> None: ...

    def drain_queued_messages(
        self, callbacks: TurnEventHandlers | None = None,
    ) -> list[QueuedMessage]:
        raise NotImplementedError

    def mark_next_turn_continuation(self, drained: list[QueuedMessage]) -> None: ...

    def drain_next_turn_text(self, callbacks: TurnEventHandlers | None = None) -> str | None:
        raise NotImplementedError

    def deliver_queued_user_message(self, callbacks: TurnEventHandlers) -> None: ...

    def _deliver_chained_hook_message(self, message: str) -> None: ...

    def _dispatch_hook(self, hook_name: str, **hook_input_kwargs: Any) -> "HookOutput":
        raise NotImplementedError

    def _dispatch_lifecycle_hook(
        self, hook_name: str, *, reason: str, workspace_just_bootstrapped: bool = False,
        include_config: bool = False,
    ) -> "HookOutput":
        raise NotImplementedError

    def fire_subagent_start_hook(self, message: str) -> str | None:
        raise NotImplementedError

    def fire_subagent_turn_end_hook(self, output: str) -> None: ...
