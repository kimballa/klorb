# © Copyright 2026 Aaron Kimball
"""`TurnBridge`: the sync/async bridge that runs a blocking `Session.send_turn()` call on a
worker thread and converts its `TurnEventHandlers` callbacks into ordered ACP `session/update`
notifications, and its blocking asks (permission, escalation, tool-call-limit) into ordered
`session/request_permission`/`_klorb/raiseToolCallLimit` round trips -- see
docs/specs/klorb-server.md's "Threading bridge" section."""

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import acp
from acp.schema import SessionInfoUpdate

from klorb.permissions.risk_classifier import (
    ItemRiskAssessment,
    record_decision_history,
    resolve_item_risk_assessment,
)
from klorb.process_config import ProcessConfig
from klorb.server.update_mapping import (
    ask_user_questions_answer_from_result,
    ask_user_questions_ext_params,
    escalate_privileges_decision_from_outcome,
    escalate_privileges_meta,
    escalate_privileges_options,
    permission_ask_meta,
    permission_ask_options,
    permission_ask_tool_call_update,
    permission_decision_from_outcome,
    permission_decision_grant_patterns,
    tool_call_finished_update,
    tool_call_started_update,
)
from klorb.session import Session, TurnEventHandlers
from klorb.session.events import (
    AskUserQuestionsAnswer,
    AskUserQuestionsItemContext,
    EscalatePrivilegesContext,
    EscalatePrivilegesDecision,
    PermissionAskContext,
    PermissionDecision,
    ToolCallEvent,
    ToolCallStartedEvent,
)
from klorb.session_naming import SessionName

logger = logging.getLogger(__name__)

_SENTINEL = object()
"""Enqueued by `run_turn()`'s `finally` to tell the pump task to stop, once the worker thread's
`Session.send_turn()` call has returned or raised -- distinct from `None`, which is never a
value a real session update could be."""

_RAISE_TOOL_CALL_LIMIT_EXT_METHOD = "klorb/raiseToolCallLimit"
"""The `_klorb/raiseToolCallLimit` extension method name, with the leading `_` that marks it as
an extension already stripped -- `acp.Client.ext_method()`'s own proxy re-adds it on the wire
(see `acp.agent.connection.AgentSideConnection.ext_method`)."""

_ASK_USER_QUESTIONS_EXT_METHOD = "klorb/askUserQuestions"
"""The `_klorb/askUserQuestions` extension method name, with the leading `_` stripped -- see
`_RAISE_TOOL_CALL_LIMIT_EXT_METHOD`'s own docstring for why."""

_USAGE_EXT_NOTIFICATION = "klorb/usage"
"""The `_klorb/usage` extension notification name, with the leading `_` stripped -- see
`_RAISE_TOOL_CALL_LIMIT_EXT_METHOD`'s own docstring for why. Sent unconditionally (no
capability gate): unlike a blocking ext *request*, an unrecognized ext *notification* is
silently ignored per ACP's own extensibility rules, so there's no wire cost to a client that
doesn't understand it."""

_AskResultT = TypeVar("_AskResultT")


class TurnBridge:
    """Runs one `Session.send_turn()` call per `run_turn()` invocation, forwarding streamed
    text and tool-call activity as ACP `session/update` notifications sent through `client`.

    Every `on_chunk`/`on_thinking_chunk`/`on_tool_call_started`/`on_tool_call` callback fires on
    `Session.send_turn()`'s worker thread (via `asyncio.to_thread`); each is enqueued onto one
    `asyncio.Queue` via `loop.call_soon_threadsafe`, and a single pump task awaits each
    `client.session_update()` call in the order the callbacks fired -- the ordering guarantee
    described in docs/specs/klorb-server.md. `on_tool_call_started`/`on_tool_call` delegate the
    klorb-event-to-ACP-update translation to `klorb.server.update_mapping`, passing this turn's
    `Session.tool_registry`/`Session.config.workspace.path` through, and additionally track a
    stack of in-flight `call_id`s so a same-turn permission/escalation ask can link itself to
    whichever call raised it.

    `on_permission_ask`/`on_escalate_privileges`/`on_tool_call_limit_reached`/
    `on_ask_user_questions` are the turn's blocking asks: each builds its `session/
    request_permission`/`_klorb/raiseToolCallLimit`/`_klorb/askUserQuestions` round trip as a
    coroutine and runs it via `call_blocking()`, which first awaits `queue.join()` (so every
    `session/update` already enqueued has actually been sent before the ask goes out -- the same
    ordering guarantee, extended to asks) and then `asyncio.run_coroutine_threadsafe(...)
    .result()`s it, parking the worker thread until the client answers. `on_permission_ask` also
    calls `klorb.permissions.risk_classifier.resolve_item_risk_assessment`/`record_decision_history`
    for a `BashTool` ask, exactly as `klorb.tui.mixins.interactions.InteractionsMixin.
    _confirm_permission_ask` does -- both call sites share that module's own gating/batching/
    caching rather than each re-implementing it (see that module's docstring).

    `run_turn()` always drains and stops the pump task in a `finally`, whether `send_turn()`
    succeeds, raises `ResponseAborted`, or raises anything else -- and, once drained, sends one
    `_klorb/usage` notification carrying the session's resulting token tally. `on_session_name_
    changed` enqueues a `session_info_update` (title) the same way `on_chunk`/`on_thinking_chunk`
    do, ordered relative to them like any other fire-and-forget callback.
    """

    def __init__(
        self, session: Session, client: acp.Client, session_id: str,
        process_config: ProcessConfig, raise_tool_call_limit_capable: bool = False,
        ask_user_questions_capable: bool = False,
    ) -> None:
        self._session = session
        self._client = client
        self._session_id = session_id
        self._process_config = process_config
        self._raise_tool_call_limit_capable = raise_tool_call_limit_capable
        self._ask_user_questions_capable = ask_user_questions_capable

    async def run_turn(self, prompt_text: str) -> str:
        """Send `prompt_text` as one turn of `self._session`'s conversation, streaming the
        reply and any reasoning text out as ACP `session/update` notifications. Returns the
        model's final response text; propagates whatever `Session.send_turn()` raises
        (including `klorb.api_provider.ResponseAborted` on a cancelled turn), after the pump
        task has fully drained and a `_klorb/usage` notification carrying the turn's resulting
        token tally has gone out -- unconditionally, whether the turn succeeded, aborted, or
        raised.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        call_id_stack: list[str] = []
        sibling_batch_index: dict[int, int] = {}

        def enqueue(update: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, update)

        def on_chunk(delta_text: str) -> None:
            enqueue(acp.update_agent_message_text(delta_text))

        def on_thinking_chunk(delta_text: str) -> None:
            enqueue(acp.update_agent_thought_text(delta_text))

        def on_session_name_changed(session_name: SessionName | None) -> None:
            enqueue(SessionInfoUpdate(
                session_update="session_info_update",
                title=session_name.title if session_name is not None else None))

        def on_tool_call_started(event: ToolCallStartedEvent) -> None:
            call_id_stack.append(event.call_id)
            enqueue(tool_call_started_update(
                event, self._session.tool_registry, self._session.config.workspace.path))

        def on_tool_call(event: ToolCallEvent) -> None:
            if call_id_stack and call_id_stack[-1] == event.call_id:
                call_id_stack.pop()
            elif event.call_id in call_id_stack:
                call_id_stack.remove(event.call_id)
            enqueue(tool_call_finished_update(
                event, self._session.tool_registry, self._session.config.workspace.path))

        def linked_call_id() -> str | None:
            return call_id_stack[-1] if call_id_stack else None

        async def run_blocking_ask(
            build_coro: Callable[[], Awaitable[_AskResultT]],
        ) -> _AskResultT:
            """Await `queue.join()` (draining every `session/update` enqueued so far) before
            running `build_coro()`, so a blocking ask never overtakes the updates that led to
            it -- see docs/specs/klorb-server.md's "Threading bridge" section."""
            await queue.join()
            return await build_coro()

        def call_blocking(build_coro: Callable[[], Awaitable[_AskResultT]]) -> _AskResultT:
            """Run `run_blocking_ask(build_coro)` on the event loop from the worker thread
            running `Session.send_turn()`, parking the worker thread until it resolves."""
            future = asyncio.run_coroutine_threadsafe(run_blocking_ask(build_coro), loop)
            return future.result()

        def on_permission_ask(ctx: PermissionAskContext) -> PermissionDecision:
            risk: ItemRiskAssessment | None = resolve_item_risk_assessment(
                ctx, session=self._session, process_config=self._process_config)
            # Keyed by the first sibling item's own identity, not `id(ctx.sibling_items)`: pydantic
            # re-wraps `sibling_items` into a fresh list on every `PermissionAskContext`
            # construction (one per item in the batch), so the *list* object's id differs across
            # calls even though the `PermissionAskItem` objects inside it -- plain objects, never
            # copied by pydantic -- stay identical.
            batch_key = id(ctx.sibling_items[0]) if ctx.sibling_items else id(ctx)
            item_index = sibling_batch_index.get(batch_key, 0)
            sibling_batch_index[batch_key] = item_index + 1
            item_total = len(ctx.sibling_items) if ctx.sibling_items is not None else 1
            options = permission_ask_options(ctx.resource)
            meta = permission_ask_meta(ctx, risk, item_index, item_total, self._session.config)
            tool_call = permission_ask_tool_call_update(linked_call_id(), ctx.resource_description)

            async def _ask() -> acp.RequestPermissionResponse:
                return await self._client.request_permission(
                    options=options, session_id=self._session_id, tool_call=tool_call, klorb=meta)

            response = call_blocking(_ask)
            grant_patterns = permission_decision_grant_patterns(ctx.resource, risk)
            decision = permission_decision_from_outcome(response.outcome, grant_patterns)
            record_decision_history(ctx, decision, session=self._session, process_config=self._process_config)
            return decision

        def on_escalate_privileges(ctx: EscalatePrivilegesContext) -> EscalatePrivilegesDecision:
            options = escalate_privileges_options()
            meta = escalate_privileges_meta(ctx)
            tool_call = permission_ask_tool_call_update(linked_call_id(), ctx.description)

            async def _ask() -> acp.RequestPermissionResponse:
                return await self._client.request_permission(
                    options=options, session_id=self._session_id, tool_call=tool_call, klorb=meta)

            response = call_blocking(_ask)
            return escalate_privileges_decision_from_outcome(response.outcome)

        def on_ask_user_questions(ctx: AskUserQuestionsItemContext) -> AskUserQuestionsAnswer:
            if not self._ask_user_questions_capable:
                logger.debug(
                    "Client did not advertise _klorb/askUserQuestions; declining question %d/%d "
                    "closed for ACP session %s", ctx.index + 1, ctx.total, self._session_id)
                return AskUserQuestionsAnswer(cancelled=True)

            params = ask_user_questions_ext_params(ctx, self._session_id)

            async def _ask() -> dict[str, Any]:
                return await self._client.ext_method(_ASK_USER_QUESTIONS_EXT_METHOD, params)

            result = call_blocking(_ask)
            return ask_user_questions_answer_from_result(ctx, result)

        def on_tool_call_limit_reached(message: str) -> bool:
            if not self._raise_tool_call_limit_capable:
                logger.debug(
                    "Client did not advertise _klorb/raiseToolCallLimit; failing the tool-call "
                    "limit closed for ACP session %s", self._session_id)
                return False

            async def _ask() -> dict[str, Any]:
                return await self._client.ext_method(
                    _RAISE_TOOL_CALL_LIMIT_EXT_METHOD,
                    {"sessionId": self._session_id, "message": message})

            result = call_blocking(_ask)
            return bool(result.get("approved", False))

        cancel_event = threading.Event()
        handlers = TurnEventHandlers(
            on_chunk=on_chunk, on_thinking_chunk=on_thinking_chunk,
            on_tool_call_started=on_tool_call_started, on_tool_call=on_tool_call,
            on_permission_ask=on_permission_ask, on_escalate_privileges=on_escalate_privileges,
            on_ask_user_questions=on_ask_user_questions,
            on_tool_call_limit_reached=on_tool_call_limit_reached,
            on_session_name_changed=on_session_name_changed, cancel_event=cancel_event)

        async def pump() -> None:
            while True:
                update = await queue.get()
                if update is _SENTINEL:
                    queue.task_done()
                    return
                await self._client.session_update(session_id=self._session_id, update=update)
                queue.task_done()

        pump_task = asyncio.create_task(pump())
        try:
            return await asyncio.to_thread(self._session.send_turn, prompt_text, handlers)
        finally:
            enqueue(_SENTINEL)
            await pump_task
            await self._client.ext_notification(_USAGE_EXT_NOTIFICATION, {
                "sessionId": self._session_id,
                "usedTokens": self._session.total_tokens_used(),
                "maxTokens": self._session.max_context_window(),
                "outputTokens": self._session.total_output_tokens_used(),
            })
