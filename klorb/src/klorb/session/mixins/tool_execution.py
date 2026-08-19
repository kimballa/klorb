# © Copyright 2026 Aaron Kimball
"""`SessionToolExecutionMixin`: dispatches every tool call requested by one model reply."""

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
from klorb.tools.exceptions import ErrorCategory, NoSuchToolException, ToolCallError
from klorb.tools.response_envelope import SystemInterjectionPayload, ToolResponseEnvelope, classify_exception
from klorb.tools.tool import describe_tool_arg_json_error

logger = logging.getLogger(__name__)


def _tool_result_text(envelope: ToolResponseEnvelope) -> str:
    """`response_body` if set, else `error_message`, else empty string."""
    value = envelope.response_body if envelope.response_body is not None else envelope.error_message
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


class SessionToolExecutionMixin(SessionBase):
    """The tool-call dispatch loop for one model round trip that requests tool use."""

    def _run_tool_calls(
        self,
        tool_use_message: Message,
        callbacks: TurnEventHandlers,
    ) -> None:
        """Execute every tool call requested by `tool_use_message`, appending one
        `role="tool_response"` Message per call carrying the result or a categorized error.
        Also fires `callbacks.on_tool_call`, if given, once per call with a `ToolCallEvent`.

        Standing interjection providers are polled once per call to this method, and any
        non-`None` results are attached as `system_interjections` on the first envelope.

        Before executing each call, checks it against `config.max_tool_calls_per_turn`.
        Reaching it asks `_confirm_limit_increase()` whether to double it and keep going;
        raising `ToolCallLimitExceeded` if declined.

        Permission asks are resolved according to `config.permission_framework`:
        `"deny"` fails closed; `"auto"` auto-approves; `"ask"` asks
        `callbacks.on_permission_ask`. `MultiPermissionAskRequired` is resolved
        item-by-item. `AskUserQuestionsRequired` is resolved via
        `callbacks.on_ask_user_questions`, with no `permission_framework` branching.
        Any failure reports the error back to the model as this call's `tool_response`.

        `call.arguments` is parsed as JSON before any of the above: a parse failure
        is reported back as this call's `tool_response` rather than aborting the whole turn.
        """
        assert self._tool_registry is not None
        assert tool_use_message.tool_calls is not None
        logger.info(
            "Dispatching %d tool call(s) requested by the model: %s",
            len(tool_use_message.tool_calls), [call.name for call in tool_use_message.tool_calls])
        system_interjections: list[SystemInterjectionPayload] = []
        for subject in sorted(self._standing_interjection_providers):
            message = self._standing_interjection_providers[subject]()
            if message is not None:
                system_interjections.append(SystemInterjectionPayload(subject=subject, body=message))
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
                    self._tool_calls_this_turn, self.config.max_tool_calls_per_turn,
                    callbacks.on_tool_call_limit_reached,
                ):
                    raise ToolCallLimitExceeded(
                        f"Exceeded {self.config.max_tool_calls_per_turn} tool call(s) in one turn.")

            self._tool_calls_this_turn += 1
            self.statistics.tool_calls += 1
            logger.info(
                "Tool call %d/%d this turn: %s(%s) [id=%s]",
                self._tool_calls_this_turn, self.config.max_tool_calls_per_turn,
                call.name, call.arguments, call.id,
            )
            args: dict[str, Any] = {}
            result: Any = None
            error: str | None = None
            category: ErrorCategory | None = None
            response_body: Any = None
            raw_arguments: str | None = None
            try:
                args = json.loads(call.arguments) if call.arguments else {}
            except json.JSONDecodeError as json_exc:
                logger.warning(
                    "Tool call %s had malformed JSON arguments (%s): %s",
                    call.name, json_exc, call.arguments)
                raw_arguments = call.arguments
                error = describe_tool_arg_json_error(call.name, raw_arguments, json_exc)
                category = "syntax"
                # Remove the offending tool call args from the tool_use message so it
                # doesn't get sent to the API on subsequent turns (which would
                # cause a 400 Bad Request error due to the invalid JSON).
                call.arguments = "{}"
                self.statistics.malformed_tool_calls += 1

            tool = None
            if error is None:
                try:
                    args = self._apply_tool_use_hook(call.name, args)
                    tool = self._tool_registry.instantiate_tool(call.name)
                    logger.debug("Tool call %s parsed arguments: %r", call.name, args)
                    if callbacks.on_tool_call_started is not None:
                        callbacks.on_tool_call_started(ToolCallStartedEvent(
                            call_id=call.id, name=call.name, args=args))
                    result = tool.apply(args)
                    logger.info("Tool call %s succeeded", call.name)
                    logger.debug("Tool call %s result: %s", call.name, result)
                except MultiPermissionAskRequired as multi_ask_exc:
                    outcome = self._resolve_multi_permission_ask(call, args, multi_ask_exc, callbacks)
                    result, error, category, response_body = (
                        outcome.result, outcome.error, outcome.category, outcome.response_body)
                except AskUserQuestionsRequired as ask_questions_exc:
                    outcome = self._resolve_ask_user_questions(call, ask_questions_exc, callbacks)
                    result, error, category, response_body = (
                        outcome.result, outcome.error, outcome.category, outcome.response_body)
                except EscalatePrivilegesRequired as escalate_exc:
                    outcome = self._resolve_escalate_privileges(call, escalate_exc, callbacks)
                    result, error, category, response_body = (
                        outcome.result, outcome.error, outcome.category, outcome.response_body)
                except PermissionAskRequired as ask_exc:
                    if not ask_exc.resource.is_persistable or self.config.permission_framework == "deny":
                        logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, ask_exc)
                        error = str(ask_exc)
                        category = "permission"
                    elif self.config.permission_framework == "auto":
                        logger.info(
                            "Auto-approving permission ask under permissionFramework=auto: %s", ask_exc)
                        decision = PermissionDecision(action="allow", scope="session")
                        outcome = self._retry_after_permission_decision(call, args, ask_exc, decision)
                        result, error, category, response_body = (
                            outcome.result, outcome.error, outcome.category, outcome.response_body)
                    elif callbacks.on_permission_ask is None:
                        logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, ask_exc)
                        error = str(ask_exc)
                        category = "permission"
                    else:
                        decision = callbacks.on_permission_ask(PermissionAskContext(
                            resource=ask_exc.resource, resource_description=str(ask_exc)))
                        outcome = self._retry_after_permission_decision(call, args, ask_exc, decision)
                        result, error, category, response_body = (
                            outcome.result, outcome.error, outcome.category, outcome.response_body)
                except NoSuchToolException:
                    logger.warning("Tool call %s: tool not found", call.name)
                    error = f"No such tool: {call.name!r}"
                    category = "validation"
                    self.statistics.unknown_tool_calls += 1
                except Exception as exc:
                    logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, exc)
                    error, category, response_body = classify_exception(exc)
            # Per-tool success/failure tracking (only when a Tool instance was resolved)
            tool_succeeded: bool | None = None
            if tool is not None:
                tool_succeeded = tool.is_success(args, result, error)
                tool_stats = self.statistics.tools.setdefault(call.name, ToolCallStats())
                if tool_succeeded:
                    tool_stats.success_count += 1
                else:
                    tool_stats.failed_count += 1
            is_first_call = call is tool_use_message.tool_calls[0]
            first_call_interjections = (
                tuple(system_interjections) if is_first_call else ())
            if error is None and tool_succeeded is False:
                # A tool whose `apply()` never raises but signals failure via its own result
                # shape (e.g. `BashTool` for a non-zero exit) -- see `Tool.is_success()`.
                envelope = ToolResponseEnvelope.error(
                    None, category="business_logic", response_body=result,
                    system_interjections=first_call_interjections)
            elif error is None:
                envelope = ToolResponseEnvelope.success(
                    result, system_interjections=first_call_interjections)
            else:
                envelope = ToolResponseEnvelope.error(
                    error, category=category, response_body=response_body,
                    system_interjections=first_call_interjections)
            envelope = self._apply_tool_result_hook(call.name, envelope)
            content = json.dumps(envelope.to_wire_dict(), ensure_ascii=False)
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
            "Finished dispatching tool calls (%d/%d this turn).",
            self._tool_calls_this_turn, self.config.max_tool_calls_per_turn,
        )

    def _apply_tool_use_hook(self, call_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Dispatch `onToolUse` before `call_name` actually runs. A `tool_args` in the
        aggregate `HookOutput` replaces `args` before the tool ever sees them;
        `success=False`, or an allow/ask/deny `permission` verdict that isn't `"allow"`,
        raises `ToolCallError`.

        `"ask"` is treated the same as `"deny"` here.
        """
        result = self._dispatch_hook("onToolUse", tool_name=call_name, tool_args=args)
        if result.tool_args is not None:
            args = result.tool_args
        if result.success is False or result.permission in ("deny", "ask"):
            raise ToolCallError(
                result.message or f"Tool call {call_name!r} blocked by onToolUse hook policy.",
                category="permission")
        return args

    def _apply_tool_result_hook(
        self, call_name: str, envelope: ToolResponseEnvelope,
    ) -> ToolResponseEnvelope:
        """Dispatch `onToolResult` after `call_name`'s `ToolResponseEnvelope` is built.
        A `tool_result` in the aggregate `HookOutput` replaces `response_body` in the
        envelope returned here."""
        tool_result = _tool_result_text(envelope)
        result = self._dispatch_hook("onToolResult", tool_name=call_name, tool_result=tool_result)
        if result.tool_result is None:
            return envelope
        if envelope.is_error and envelope.response_body is None:
            return envelope.model_copy(update={"error_message": result.tool_result})
        return envelope.model_copy(update={"response_body": result.tool_result})
