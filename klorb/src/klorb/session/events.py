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
    """The outcome of resolving one tool call's ask-style exception (a permission ask,
    `AskUserQuestions`, or `EscalatePrivileges`), returned by every `_resolve_*`/`_retry_after_*`
    method in `klorb.session.mixins.permissions`. Replaces a bare `tuple[Any, str | None]` --
    see `.claude/skills/encapsulate-in-classes/SKILL.md`'s guidance against returning
    loosely-related values positionally.

    `result` and `response_body` are mutually exclusive by convention, mirroring
    `klorb.tools.response_envelope.ToolResponseEnvelope`: `result` is meaningful only when
    `error is None` (a successful resolution); `response_body` only when it's not (a failure
    whose exception carried its own payload, e.g. a retried call's `ToolCallError`)."""

    result: Any = None
    error: str | None = None
    category: ErrorCategory | None = None
    response_body: Any = None


class PermissionAskContext(BaseModel):
    """Passed to `on_permission_ask` once per item needing a decision — either from a plain
    `PermissionAskRequired` (always exactly one item) or a `MultiPermissionAskRequired` (asked
    about one at a time, in order — see `Session._run_tool_calls`): the resolved candidate
    resource and a human-readable description of the access, for a UI to build its prompt from
    without re-parsing the raised exception's message string.

    `resource` is the `klorb.permissions.resource.PermissionResource` this ask is about, mirroring
    `klorb.permissions.table.PermissionAskItem.resource` — see that module for the polymorphic
    methods a UI/session consumer calls on it instead of branching on its concrete kind.

    `bash_context`, when set, mirrors `PermissionAskItem.bash_context`: the
    `klorb.permissions.resource.BashCommandContext` a `BashTool`-originated ask carries, regardless
    of `resource`'s own kind — its `command_text` is the full raw command string the item's own
    `resource_description` detail belongs to, and `item_command_text` is the exact source text of
    just the one statement this item is actually about, which a UI should prefer for its prominent
    per-item preview (see `BashCommandContext`'s own docstring for the full field-by-field
    rationale). `None` for a non-`BashTool` ask.

    `sibling_items`, set by `Session._resolve_multi_permission_ask` to the full
    `MultiPermissionAskRequired.items` list (including the item this context is itself about, in
    the same order), lets a UI batch work across a whole compound command's several asks even
    though they're each still asked about one at a time, in series — e.g.
    `klorb.tui.ReplApp._confirm_permission_ask` uses it to send `klorb.permissions.
    risk_classifier.classify_command_risk()` every item in one request the first time any of
    them is seen, rather than once per item. `None` for a plain single-item
    `PermissionAskRequired` ask (that path never has `bash_context` set at all, so there is
    nothing for a command-risk classifier to batch there in the first place). `Session` itself
    never reads this field back — it only threads data it already has for whichever callback
    consumes it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    resource: PermissionResource
    bash_context: BashCommandContext | None = None
    resource_description: str
    sibling_items: list[PermissionAskItem] | None = None


class PermissionDecision(BaseModel):
    """The user's answer to one `PermissionAskContext` prompt, returned by `on_permission_ask`.

    `action` and `scope` are independent axes: `"allow"`/`"deny"` cross with `"once"` (retries
    without persisting anything, via a one-shot `ToolSetupContext.permission_override` the ask
    item's own `PermissionResource.added_to_override()` builds — see
    `klorb.permissions.resource.PermissionOverride`) and `"session"`/`"workspace"`/`"homedir"`
    (persistent for a resource whose `is_persistable` is `True` — `Session.
    _retry_after_permission_decision`/`_retry_after_multi_permission_decisions` apply the grant
    itself via the resource's own `apply_grant()`, before retrying; a `StructuralResource` item's
    `is_persistable` is `False`, since it identifies no filesystem path, command pattern, skill,
    or domain — `on_permission_ask` only needs to ask the user and report their choice back, never
    apply or persist anything itself).
    `other_text`, if set, means the user typed free-text instead of picking a grid cell —
    equivalent to `action="deny", scope="once"` but with the explanation surfaced to the model
    alongside the denial, so it can act on the redirection without a second round trip.

    `grant_patterns`, when set, is the exact wildcard-pattern rule(s) a persistent grant for this
    item must be recorded at, in place of recomputing one from the item's own raw resource after
    the fact — today only meaningful for a `CommandResource` item's `commandRules` pattern (the
    same list a UI showed the user as "grants: ..." at ask time — `klorb.tui.mixins.interactions.
    ReplApp._confirm_permission_ask` builds it from `klorb.permissions.risk_classifier.
    ItemRiskAssessment.suggested_pattern` when the risk classifier offered one, else from
    `CommandResource.grant_preview()`), but named generically rather than `command_patterns` since
    any future pattern/wildcard-based grant kind (not just `commandRules`) would reuse this same
    field rather than growing its own. Threading it through
    here — rather than having `apply_command_permission_grant` recompute a pattern from the raw
    resource after the fact — is what keeps the persisted grant identical to what was displayed:
    recomputing from argv alone has no way to know about a risk-classifier-suggested wildcard,
    since that pattern doesn't correspond to any existing `ask`-category rule the recomputation
    could find. `None` for every item with no pattern-based grant of its own (every
    non-`CommandResource` item today), and for a `CommandResource` item whenever the caller has no
    precomputed patterns to offer (in which case `apply_command_permission_grant` falls back to
    computing them itself)."""

    action: Literal["allow", "deny"]
    scope: Literal["once", "session", "workspace", "homedir"] = "once"
    other_text: str | None = None
    grant_patterns: list[list[str]] | None = None


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


class EscalatePrivilegesContext(BaseModel):
    """Passed to `on_escalate_privileges` when the `EscalatePrivileges` tool requests a
    session-only privilege grant (see `klorb.tools.escalate_privileges`). `scope` is the
    requested scope string (today, only `"workspace"`); `description` is a human-readable
    explanation of what approving would unlock, for a UI to show without re-deriving it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    scope: str
    description: str


class EscalatePrivilegesDecision(BaseModel):
    """The user's answer to an `EscalatePrivilegesContext` prompt, returned by
    `on_escalate_privileges`. `approved` is `True` when the user granted the scope for the
    rest of the session (Session records it into `SessionConfig.approved_scopes`); `False`
    when denied, so the privileged-path deny stays in effect and the tool reports the denial
    back to the model."""

    approved: bool = False


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


class TurnEventHandlers(BaseModel):
    """Immutable bundle of the optional callbacks (and cancellation signal) a caller can
    supply for one turn: `on_chunk`/`on_thinking_chunk`/`on_reasoning_details` (streamed
    response text, reasoning text, and structured reasoning payload respectively),
    `cancel_event` (abort a turn mid-stream), `on_tool_call_limit_reached` (ask whether to
    raise a safety cap), `on_permission_ask` (ask how to resolve an `\"ask\"` permission
    verdict), `on_ask_user_questions` (ask the user one `AskUserQuestions` tool-call
    question), `on_escalate_privileges` (ask the user to approve a session-only
    `EscalatePrivileges` grant), `on_tool_call_started` (report a tool call about to run,
    for a running indicator), and `on_tool_call` (report one finished tool call, for
    display). `on_session_name_changed` fires once, at most, on the first `send_turn()` call for
    a `Session` -- with the derived `SessionName` if the naming classifier succeeded, or `None`
    if it failed -- see `SessionCoreMixin._run_session_naming`. Replaces passing these as
    separate keyword arguments through `send_turn()`/`retry_last_turn()`/`_dispatch_turn()` and
    everything they call — a single object here means a future addition only touches this
    class, not every method's signature along the chain. `frozen=True` since a
    `TurnEventHandlers` is built once per turn and never mutated; `arbitrary_types_allowed=True`
    is needed for the `threading.Event` field (`Callable` fields validate natively without it).
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


class UserSkillActivation(BaseModel):
    """The result of resolving a prompt's leading `/<token>` mention to an unconditional skill
    activation -- see `Session._build_user_skill_activation_interjection`. `body` and `skill_id`
    always travel together (there is no "interjection text without a skill identity" state to
    accidentally produce): `body` is the text `send_turn()` wraps in a `UserSkillActivation`
    `<SystemInterjection>`; `skill_id` is the skill's canonical `(namespace, name)`, for
    `_build_skill_reference_interjection` to exclude it from the turn's ordinary reminder."""

    model_config = ConfigDict(frozen=True)

    body: str
    skill_id: tuple[str, str]
