# © Copyright 2026 Aaron Kimball
"""Pydantic types exchanged between `Session` and a caller's `TurnEventHandlers` callbacks:
the `*Context`/`*Decision`/`*Answer`/`*Event` pairs for permission asks, `AskUserQuestions`,
`EscalatePrivileges`, and finished/started tool calls, plus the `TurnEventHandlers` bundle
itself, `UserSkillActivation`, and `ToolCallOutcome`."""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from klorb.permissions.resource import BashCommandContext, PermissionResource
from klorb.permissions.table import PermissionAskItem
from klorb.session_naming import SessionName
from klorb.tools.ask.common import QuestionOption
from klorb.tools.exceptions import ErrorCategory


@dataclass
class ToolCallOutcome:
    """The outcome of resolving one tool call's ask-style exception, returned by every
    `_resolve_*`/`_retry_after_*` method in `klorb.session.mixins.permissions`. Replaces a
    bare `tuple[Any, str | None]`.

    `result` and `response_body` are mutually exclusive by convention: `result` is meaningful
    only when `error is None`; `response_body` only when it's not."""

    result: Any = None
    error: str | None = None
    category: ErrorCategory | None = None
    response_body: Any = None


class PermissionAskContext(BaseModel):
    """Passed to `on_permission_ask` once per item needing a decision.

    `resource` is the `klorb.permissions.resource.PermissionResource` this ask is about.

    `bash_context`, when set, is the `klorb.permissions.resource.BashCommandContext` a
    `BashTool`-originated ask carries, regardless of `resource`'s own kind. `None` for a
    non-`BashTool` ask.

    `sibling_items`, set by `Session._resolve_multi_permission_ask` to the full
    `MultiPermissionAskRequired.items` list (including the item this context is itself about, in
    the same order), lets a UI batch work across a whole compound command's several asks even
    though they're each still asked about one at a time, in series. `None` for a plain
    single-item `PermissionAskRequired` ask. `Session` itself never reads this field back.

    `origin_session_id`, set by `klorb.agents.policy.build_subagent_turn_handlers` when this ask
    was raised from inside a subagent's turn (`None` for the root session's own turn), identifies
    which `Session` in the tree actually needs to answer it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    resource: PermissionResource
    bash_context: BashCommandContext | None = None
    resource_description: str
    sibling_items: list[PermissionAskItem] | None = None
    origin_session_id: str | None = None


class PermissionDecision(BaseModel):
    """The user's answer to one `PermissionAskContext` prompt, returned by `on_permission_ask`.

    `action` and `scope` are independent axes: `"allow"`/`"deny"` cross with `"once"` and
    `"session"`/`"workspace"`/`"homedir"`.

    `other_text`, if set, means the user typed free-text instead of picking a grid cell.

    `grant_patterns`, when set, is the exact wildcard-pattern rule(s) a persistent grant for this
    item must be recorded at, in place of recomputing one from the item's own raw resource after
    the fact. Threading it through here — rather than having `apply_command_permission_grant`
    recompute a pattern from the raw resource after the fact — is what keeps the persisted grant
    identical to what was displayed. `None` for every item with no pattern-based grant of its
    own, and for a `CommandResource` item whenever the caller has no precomputed patterns to
    offer."""

    action: Literal["allow", "deny"]
    scope: Literal["once", "session", "workspace", "homedir"] = "once"
    other_text: str | None = None
    grant_patterns: list[list[str]] | None = None


class AskUserQuestionsItemContext(BaseModel):
    """Passed to `on_ask_user_questions` once per question in an `AskUserQuestionsRequired`
    batch, asked about one at a time, in order. `index`/`total` let a UI render "Question 2
    of 3" without re-deriving it from a running count of its own calls."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    header: str
    question: str
    options: list[QuestionOption]
    index: int
    total: int
    origin_session_id: str | None = None


class AskUserQuestionsAnswer(BaseModel):
    """The user's answer to one `AskUserQuestionsItemContext` prompt, returned by
    `on_ask_user_questions`. `answer` is the final rendered string for a selected option
    or the user's raw free-text answer; it is `None` only when `cancelled` is set."""

    answer: str | None = None
    cancelled: bool = False


class EscalatePrivilegesContext(BaseModel):
    """Passed to `on_escalate_privileges` when the `EscalatePrivileges` tool requests a
    session-only privilege grant. `scope` is the requested scope string; `description` is a
    human-readable explanation of what approving would unlock. `reason` is the model-supplied
    explanation of why it needs the grant."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    scope: str
    description: str
    reason: str
    origin_session_id: str | None = None


class EscalatePrivilegesDecision(BaseModel):
    """The user's answer to an `EscalatePrivilegesContext` prompt, returned by
    `on_escalate_privileges`. `approved` is `True` when the user granted the scope for the
    rest of the session; `False` when denied, so the privileged-path deny stays in effect
    and the tool reports the denial back to the model."""

    approved: bool = False


FileAccessMode = Literal["read", "write"]
"""Whether a `ReadFile`/`EditFile`/`CreateFile` call reported to `TurnEventHandlers.
on_file_accessed` read its subject or wrote it (an edit or a fresh create alike)."""


class ToolCallEvent(BaseModel):
    """Reports one finished tool call to `TurnEventHandlers.on_tool_call`, fired once per call
    from `_run_tool_calls` right after it completes. Carries raw data — the parsed call
    arguments and either the tool's raw (non-JSON-stringified) return value or a failure
    description — rather than pre-rendered display strings, so `Session` stays entirely
    ignorant of how a call is displayed; a consumer renders `name`/`args`/`result`/`error`
    itself.
    """

    model_config = ConfigDict(frozen=True)

    call_id: str
    name: str
    args: dict[str, Any]
    result: Any = None
    """The tool's raw return value from `apply()` when `error is None`; meaningless when the
    call failed."""
    error: str | None = None
    """Human-readable failure description when the call failed, `None` on success."""
    raw_arguments: str | None = None
    """The model's unparsed `arguments` string, set only when it failed to parse as JSON."""


class ToolCallStartedEvent(BaseModel):
    """Reports that a tool call is about to start executing, to
    `TurnEventHandlers.on_tool_call_started`, fired once per call from `_run_tool_calls`
    right before `tool.apply(args)`. Carries the same `call_id`/`name`/`args` the later
    `ToolCallEvent` will carry, so a UI can link the "started" widget to its eventual
    completion and show a running indicator before the tool's actual work begins.
    """

    model_config = ConfigDict(frozen=True)

    call_id: str
    name: str
    args: dict[str, Any]


class QueuedMessage(BaseModel):
    """A message queued for delivery as the next turn once the current one ends.

    `message_text` is the raw text to send. `origin` records where it came from.
    `history_data` is an opaque field the UI layer can use to store widget references or other
    state needed to transition the queued message's visual representation when it's eventually
    dispatched. The Session layer treats this field as a black box.
    """

    model_config = ConfigDict(frozen=False, arbitrary_types_allowed=True)

    message_text: str
    origin: Literal["user", "chained_hook", "event"] = "user"
    history_data: Any = None


class TurnEventHandlers(BaseModel):
    """Immutable bundle of the optional callbacks a caller can supply for one turn:
    `on_chunk`/`on_thinking_chunk`/`on_reasoning_details`, `cancel_event`,
    `on_tool_call_limit_reached`, `on_permission_ask`, `on_ask_user_questions`,
    `on_escalate_privileges`, `on_tool_call_started`, and `on_tool_call`.
    `on_session_name_changed` fires once, at most, on the first `send_turn()` call for a
    `Session`. `on_skill_activated` fires with a skill's `(namespace, name)` identity when the
    turn's prompt leads with a `/<name>` mention that unconditionally activates it, so a caller
    can surface "Activated skill: ..." without re-parsing the interjection out of the stored
    message content. `on_file_accessed` fires once per successful `ReadFile`/`EditFile`/
    `CreateFile` call with the resolved absolute path and whether it was a read or a write.
    Replaces passing these as separate keyword arguments through
    `send_turn()`/`retry_last_turn()`/`_dispatch_turn()` and everything they call.
    `frozen=True` since a `TurnEventHandlers` is built once per turn and never mutated;
    `arbitrary_types_allowed=True` is needed for the `threading.Event` field.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    on_chunk: Callable[[str], None] | None = None
    on_thinking_chunk: Callable[[str], None] | None = None
    on_reasoning_details: Callable[[list[dict[str, Any]]], None] | None = None
    cancel_event: threading.Event | None = None
    on_tool_call_limit_reached: Callable[[str], bool] | None = None
    on_permission_ask: Callable[[PermissionAskContext], PermissionDecision] | None = None
    on_ask_user_questions: (
        Callable[[AskUserQuestionsItemContext], AskUserQuestionsAnswer] | None
    ) = None
    on_escalate_privileges: (
        Callable[[EscalatePrivilegesContext], EscalatePrivilegesDecision] | None
    ) = None
    on_tool_call_started: Callable[[ToolCallStartedEvent], None] | None = None
    on_tool_call: Callable[[ToolCallEvent], None] | None = None
    on_session_name_changed: Callable[[SessionName | None], None] | None = None
    on_skill_activated: Callable[[tuple[str, str]], None] | None = None
    on_file_accessed: Callable[[str, FileAccessMode], None] | None = None
    on_enqueue_message: Callable[["QueuedMessage"], None] | None = None
    on_send_queued_message: Callable[["QueuedMessage"], None] | None = None


class UserSkillActivation(BaseModel):
    """The result of resolving a prompt's leading `/<token>` mention to an unconditional skill
    activation. `body` and `skill_id` always travel together: `body` is the text `send_turn()`
    wraps in a `UserSkillActivation` `<SystemInterjection>`; `skill_id` is the skill's
    canonical `(namespace, name)`, for `_build_skill_reference_interjection` to exclude it
    from the turn's ordinary reminder."""

    model_config = ConfigDict(frozen=True)

    body: str
    skill_id: tuple[str, str]
