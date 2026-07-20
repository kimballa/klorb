# © Copyright 2026 Aaron Kimball
"""The `klorb.session` package: `Session`, the per-conversation state and turn-dispatch loop
shared by the one-shot prompt path and the interactive REPL, plus the config/event types every
caller exchanges with it.

`Session` itself is assembled from the mixins in `klorb.session.mixins` -- construction and
simple accessors (`core`), system-prompt/message-bookkeeping setup (`prompt_setup`),
skill/context-file interjection building (`skills`), ask-exception resolution (`permissions`),
the tool-call dispatch loop (`tool_execution`), and the turn-dispatch loop itself (`turns`) --
the same shape `klorb.tui.app.ReplApp` uses to split up its own mixins. See
`klorb.session.mixins._base.SessionBase` for why every mixin declares itself against a shared
base class instead of subclassing `Session` piecewise.
"""

from klorb.session.config import SessionConfig
from klorb.session.constants import (
    DEFAULT_MAX_TOOL_CALLS_PER_SESSION,
    DEFAULT_MAX_TOOL_CALLS_PER_TURN,
    MAX_TOOL_CALL_ROUNDS,
    NONCE_WORD_COUNT,
    PERMISSION_FRAMEWORK_INTERJECTIONS,
    SESSION_ID_TIMESTAMP_FORMAT,
    THINKING_EFFORT_TOKEN_BUDGETS,
    PermissionFramework,
    ThinkingEffort,
    ToolCallLimitExceeded,
    generate_session_id,
)
from klorb.session.events import (
    AskUserQuestionsAnswer,
    AskUserQuestionsItemContext,
    EscalatePrivilegesContext,
    EscalatePrivilegesDecision,
    PermissionAskContext,
    PermissionDecision,
    ToolCallEvent,
    ToolCallStartedEvent,
    TurnEventHandlers,
    UserSkillActivation,
)
from klorb.session.mixins._base import SessionBase
from klorb.session.mixins.core import SessionCoreMixin
from klorb.session.mixins.permissions import SessionPermissionsMixin
from klorb.session.mixins.prompt_setup import SessionPromptSetupMixin
from klorb.session.mixins.skills import SessionSkillsMixin
from klorb.session.mixins.tool_execution import SessionToolExecutionMixin
from klorb.session.mixins.turns import SessionTurnsMixin


class Session(
    SessionCoreMixin,
    SessionPromptSetupMixin,
    SessionSkillsMixin,
    SessionPermissionsMixin,
    SessionToolExecutionMixin,
    SessionTurnsMixin,
    SessionBase,
):
    """Holds the state for the active klorb session and runs its turn-based loop.

    Both the one-shot prompt path and the interactive REPL send their prompts through a
    `Session`: each call to `send_turn()` is one turn of that loop, regardless of whether
    it's the only turn (one-shot) or one of many (REPL).
    """


__all__ = [
    "DEFAULT_MAX_TOOL_CALLS_PER_SESSION",
    "DEFAULT_MAX_TOOL_CALLS_PER_TURN",
    "MAX_TOOL_CALL_ROUNDS",
    "NONCE_WORD_COUNT",
    "PERMISSION_FRAMEWORK_INTERJECTIONS",
    "SESSION_ID_TIMESTAMP_FORMAT",
    "THINKING_EFFORT_TOKEN_BUDGETS",
    "AskUserQuestionsAnswer",
    "AskUserQuestionsItemContext",
    "EscalatePrivilegesContext",
    "EscalatePrivilegesDecision",
    "PermissionAskContext",
    "PermissionDecision",
    "PermissionFramework",
    "Session",
    "SessionConfig",
    "ThinkingEffort",
    "ToolCallEvent",
    "ToolCallLimitExceeded",
    "ToolCallStartedEvent",
    "TurnEventHandlers",
    "UserSkillActivation",
    "generate_session_id",
]
