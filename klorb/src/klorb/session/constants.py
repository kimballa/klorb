# © Copyright 2026 Aaron Kimball
"""Module-level constants, literals, and the session-id generator shared by `klorb.session`'s
mixins -- separated out so they can be imported without pulling in `Session` itself."""

from datetime import datetime
from typing import Literal

from coolname import generate_slug

MAX_TOOL_CALL_ROUNDS = 200
"""Safety cap on how many model-to-tool round trips one turn will run before giving up, in
case a model gets stuck repeatedly requesting tool calls without ever returning a final
answer. Unlike `SessionConfig.max_tool_calls_per_turn`, this isn't user-configurable or
interactively raisable — it's a hard backstop."""

DEFAULT_MAX_TOOL_CALLS_PER_TURN = 100
"""Default value of `SessionConfig.max_tool_calls_per_turn`: how many individual tool calls
(across every round) one turn will execute before asking the user whether to keep going (see
`Session._confirm_limit_increase`)."""

DEFAULT_MAX_CHAINED_HOOK_TURNS = 5
"""Default value of `SessionConfig.max_chained_hook_turns`: how many turns in a row an
`onAgentTurnEnd`/`onSubagentTurnEnd` `chat` handler may auto-chain (via
`Session._deliver_chained_hook_message`) before further auto-chained turns are refused, until
a real user-driven turn resets the count -- the same fail-safe shape as
`DEFAULT_MAX_TOOL_CALLS_PER_TURN`, guarding against a misconfigured handler that keeps the
agent talking to itself indefinitely. `0` disables chaining entirely; a negative value means
no cap."""


class ToolCallLimitExceeded(Exception):
    """Raised when a turn exceeds one of the tool-calling safety caps without the model
    returning a final answer: more than `MAX_TOOL_CALL_ROUNDS` model-to-tool round trips, or
    the user declined to raise `max_tool_calls_per_turn` past where it was exceeded (see
    `Session._confirm_limit_increase`)."""


class ChainedHookMessageUndeliverableError(Exception):
    """Raised by `Session.deliver_event_message` when an event handler's message arrives while
    the session is fully idle (no turn in flight) and no host has registered a wake handler
    (see `Session.register_wake_handler`)."""


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
