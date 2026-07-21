# © Copyright 2026 Aaron Kimball
"""`SessionToolExecutionMixin`: dispatches every tool call requested by one model reply,
resolving whichever ask-style exception (`PermissionAskRequired`,
`MultiPermissionAskRequired`, `AskUserQuestionsRequired`, `EscalatePrivilegesRequired`) a call
raises via `klorb.session.mixins.permissions.SessionPermissionsMixin`."""

import json
import logging
from datetime import datetime
from typing import Any

from klorb.api_provider import ResponseAborted
from klorb.message import Message
from klorb.permissions.table import MultiPermissionAskRequired, PermissionAskRequired
from klorb.session.constants import ToolCallLimitExceeded
from klorb.session.events import (
    PermissionAskContext,
    PermissionDecision,
    ToolCallEvent,
    ToolCallStartedEvent,
    TurnEventHandlers,
)
from klorb.session.mixins._base import SessionBase
from klorb.session_statistics import ToolCallStats
from klorb.token_estimate import estimate_tokens
from klorb.tool_call_log import log_tool_call
from klorb.tools.ask.common import AskUserQuestionsRequired
from klorb.tools.escalate_privileges.common import EscalatePrivilegesRequired
from klorb.tools.exceptions import NoSuchToolException
from klorb.tools.tool import describe_tool_arg_json_error

logger = logging.getLogger(__name__)


def _format_tool_response_content(result: Any, error: str | None) -> str:
    """Render a tool call's `(result, error)` pair (see `ToolCallEvent`) as the string stored
    in its `role="tool_response"` `Message.content`: `"Error: {error}"` on failure, otherwise
    `result` unchanged if it's already a string, else its JSON encoding.
    """
    if error is not None:
        return f"Error: {error}"
    return result if isinstance(result, str) else json.dumps(result)


class SessionToolExecutionMixin(SessionBase):
    """The tool-call dispatch loop `_dispatch_turn` runs once per model round trip that
    requests tool use."""

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
        is set, also appends the call's name/arguments and result/error to `$KLORB_STATE_DIR/tool-calls.log`
        via
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

        If a call raises `PermissionAskRequired` with a `path` or `url`, how it's resolved
        depends on `config.permission_framework` (see `SessionConfig.permission_framework`):
        `"deny"` fails closed without invoking any callback; `"auto"` auto-approves via a
        synthesized `PermissionDecision(action="allow", scope="session")`, also without
        invoking any callback; `"ask"` asks `callbacks.on_permission_ask`, if given, for a
        `PermissionDecision` and retries or denies accordingly (see
        `_retry_after_permission_decision`) — with no callback (e.g. a headless one-shot run
        left at the `"ask"` default), it fails closed the same as `"deny"`. An exception with
        no `path`, `skill`, or `url` (a call site that didn't supply one) always fails closed,
        regardless of `permission_framework`.

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
        doesn't take down every other call/round in flight. The `tool_response`'s text comes
        from `klorb.tools.tool.describe_tool_arg_json_error()`, a teaching message (break-point
        offset, XML detection, common JSON mistakes, an edit-argument-specific escaping nudge)
        rather than the bare decode-error string. `ToolCallEvent.raw_arguments` carries the
        unparsed string in this case, for a UI to display.
        """
        assert self._tool_registry is not None
        assert tool_use_message.tool_calls is not None
        logger.info(
            "Dispatching %d tool call(s) requested by the model: %s",
            len(tool_use_message.tool_calls), [call.name for call in tool_use_message.tool_calls])
        for call in tool_use_message.tool_calls:
            if callbacks.cancel_event is not None and callbacks.cancel_event.is_set():
                # Stop before running the next call (and before asking about any more of this
                # round's calls) once cancellation is signalled -- see the matching round-boundary
                # check in `_dispatch_turn` and `TurnEventHandlers.cancel_event`. Calls already
                # dispatched in this round keep their `tool_response` messages, exactly like a
                # mid-stream abort leaves an earlier round's completed calls in place.
                raise ResponseAborted()
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
            self.statistics.tool_calls += 1
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
                error = describe_tool_arg_json_error(call.name, raw_arguments, json_exc)
                # Remove the offending tool call args from the tool_use message so it
                # doesn't get sent to the API on subsequent turns (which would
                # cause a 400 Bad Request error due to the invalid JSON).
                call.arguments = "{}"
                self.statistics.malformed_tool_calls += 1

            tool = None
            if error is None:
                try:
                    tool = self._tool_registry.instantiate_tool(call.name)
                    logger.debug("Tool call %s parsed arguments: %r", call.name, args)
                    if callbacks.on_tool_call_started is not None:
                        callbacks.on_tool_call_started(ToolCallStartedEvent(
                            call_id=call.id, name=call.name, args=args))
                    result = tool.apply(args)
                    logger.info("Tool call %s succeeded: %s", call.name, result)
                except MultiPermissionAskRequired as multi_ask_exc:
                    result, error = self._resolve_multi_permission_ask(call, args, multi_ask_exc, callbacks)
                except AskUserQuestionsRequired as ask_questions_exc:
                    result, error = self._resolve_ask_user_questions(call, ask_questions_exc, callbacks)
                except EscalatePrivilegesRequired as escalate_exc:
                    result, error = self._resolve_escalate_privileges(call, escalate_exc, callbacks)
                except PermissionAskRequired as ask_exc:
                    if not ask_exc.resource.is_persistable or self.config.permission_framework == "deny":
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
                            resource=ask_exc.resource, resource_description=str(ask_exc)))
                        result, error = self._retry_after_permission_decision(call, args, ask_exc, decision)
                except NoSuchToolException:
                    logger.warning("Tool call %s: tool not found", call.name)
                    error = f"No such tool: {call.name!r}"
                    self.statistics.unknown_tool_calls += 1
                except Exception as exc:
                    logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, exc)
                    error = str(exc)
            # Per-tool success/failure tracking (only when a Tool instance was resolved)
            if tool is not None:
                tool_stats = self.statistics.tools.setdefault(call.name, ToolCallStats())
                if tool.is_success(args, result, error):
                    tool_stats.success_count += 1
                else:
                    tool_stats.failed_count += 1
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
                num_tokens=estimate_tokens(content),
                processing_state="complete",
                timestamp=datetime.now(),
                tool_call_id=call.id,
            ))
        logger.info(
            "Finished dispatching tool calls (%d/%d this turn, %d/%d this session).",
            self._tool_calls_this_turn, self.config.max_tool_calls_per_turn,
            self._tool_calls_this_session, self.config.max_tool_calls_per_session,
        )
