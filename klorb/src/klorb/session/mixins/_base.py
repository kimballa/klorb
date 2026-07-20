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
from typing import TYPE_CHECKING, Any, Literal

from klorb.api_provider import ApiProvider
from klorb.message import Message, ToolCallRequest
from klorb.models.model import CacheMgmtStyle, Model
from klorb.models.registry import ModelRegistry
from klorb.permissions.table import MultiPermissionAskRequired, PermissionAskRequired
from klorb.role import Role
from klorb.session.config import SessionConfig
from klorb.session.constants import ThinkingEffort
from klorb.session.events import PermissionDecision, TurnEventHandlers, UserSkillActivation
from klorb.session_statistics import SessionStatistics
from klorb.system_prompt import SystemPrompt
from klorb.tools.ask.common import AskUserQuestionsRequired
from klorb.tools.escalate_privileges.common import EscalatePrivilegesRequired
from klorb.tools.scratchpad.common import Scratchpad
from klorb.tools.skill.model import Skill

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
    _session_name: str | None
    _role: Role
    _provider: ApiProvider
    _model_registry: ModelRegistry
    _system_prompt: SystemPrompt
    _process_config: "ProcessConfig | None"
    _thinking_token_budgets: dict[ThinkingEffort, int]
    _tool_registry: "ToolRegistry | None"
    tool_state: dict[str, Any]
    active_cancel_event: threading.Event | None
    _tool_calls_this_session: int
    _tool_calls_this_turn: int
    _compatibility_claude_markdown: bool
    _compatibility_claude_skills: bool
    _log_tool_calls: bool
    _messages: list[Message]
    _skills_seeded: bool
    _context_files_seeded: bool
    _metadata_seeded: bool
    _session_started_at: datetime
    _pending_permission_framework_interjection: str | None
    _standing_interjection_providers: dict[str, Callable[[], str | None]]
    _teardown_callbacks: dict[str, Callable[[], None]]
    scratchpad: Scratchpad
    statistics: SessionStatistics

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

    def _ensure_skill_catalog(self) -> None: ...

    def _discover_skills(self) -> list[Skill]:
        raise NotImplementedError

    def _build_context_files_interjection(self) -> str | None:
        raise NotImplementedError

    def _build_available_skills_interjection(self, skills: list[Skill]) -> str | None:
        raise NotImplementedError

    def _build_skill_reference_interjection(
        self, tokens: list[str], *, exclude: frozenset[tuple[str, str]] = frozenset(),
    ) -> str | None:
        raise NotImplementedError

    def _build_user_skill_activation_interjection(self, token: str) -> UserSkillActivation | None:
        raise NotImplementedError

    def _confirm_limit_increase(
        self,
        scope: Literal["turn", "session"],
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
    ) -> tuple[Any, str | None]:
        raise NotImplementedError

    def _resolve_multi_permission_ask(
        self,
        call: ToolCallRequest,
        args: dict[str, Any],
        multi_ask_exc: MultiPermissionAskRequired,
        callbacks: TurnEventHandlers,
    ) -> tuple[Any, str | None]:
        raise NotImplementedError

    def _resolve_ask_user_questions(
        self,
        call: ToolCallRequest,
        ask_exc: AskUserQuestionsRequired,
        callbacks: TurnEventHandlers,
    ) -> tuple[Any, str | None]:
        raise NotImplementedError

    def _resolve_escalate_privileges(
        self,
        call: ToolCallRequest,
        escalate_exc: EscalatePrivilegesRequired,
        callbacks: TurnEventHandlers,
    ) -> tuple[Any, str | None]:
        raise NotImplementedError

    def _run_tool_calls(
        self,
        tool_use_message: Message,
        callbacks: TurnEventHandlers,
    ) -> None: ...
