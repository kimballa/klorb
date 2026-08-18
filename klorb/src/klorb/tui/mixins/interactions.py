# © Copyright 2026 Aaron Kimball
"""InteractionsMixin: the permission-ask, ask-user-questions, and escalate-privileges
confirmation flows for ReplApp."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TypeVar

from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from klorb.permissions.resource import CommandResource, GrantPreview
from klorb.permissions.risk_classifier import record_decision_history, resolve_item_risk_assessment
from klorb.session import (
    AskUserQuestionsAnswer,
    AskUserQuestionsItemContext,
    EscalatePrivilegesContext,
    EscalatePrivilegesDecision,
    PermissionAskContext,
    PermissionDecision,
)
from klorb.tui._base import ReplAppBase
from klorb.tui.constants import HISTORY_ID, INTERACTION_PANEL_ID, PROMPT_INPUT_ID
from klorb.tui.panels.ask_user_questions_panel import AskUserQuestionsPanel, format_ask_user_questions_answer
from klorb.tui.panels.escalate_privileges_panel import (
    EscalatePrivilegesPanel,
    format_escalate_privileges_decision,
)
from klorb.tui.panels.permission_ask_panel import (
    PermissionAskPanel,
    format_ask_context_body,
    format_permission_decision,
)
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.tool_call_widgets import ToolCallLimitScreen

INTERACTION_RECORD_LABEL = "<Approval>"

_COMMAND_PREVIEW_WIDTH_PADDING = 4
"""Horizontal space `PermissionAskPanel`'s own `padding: 1 2` consumes around its command
preview."""
_MIN_COMMAND_PREVIEW_WRAP_WIDTH = 20
"""Floor for the wrap-width estimate above, so a very narrow terminal still gets a usable preview
rather than a degenerate near-zero width."""

_InteractionResult = TypeVar("_InteractionResult")
"""The decision/answer type an interaction panel resolves its future with."""


class InteractionsMixin(ReplAppBase):
    """Permission-ask/ask-user-questions/escalate-privileges confirm flows, plus the
    tool-call-limit confirmation and shared interaction-panel lifecycle helpers."""

    async def _confirm_tool_call_limit(self, message: str) -> bool:
        """Show `ToolCallLimitScreen` with `message` and wait for the user's yes/no answer."""
        return await self.push_screen_wait(ToolCallLimitScreen(message))

    def _on_tool_call_limit_reached(self, message: str) -> bool:
        """`Session`'s `on_tool_call_limit_reached` callback: block the worker thread running
        `Session.send_turn()` until the user answers `ToolCallLimitScreen(message)`.
        """
        # mypy can't solve App.call_from_thread's `CallThreadReturnType` TypeVar against a
        # `Callable[[str], Coroutine[Any, Any, bool]]` argument (a stub-modeling limitation,
        # not a real type error: Textual's own runtime `invoke()` awaits coroutine callbacks
        # like any other `Callable[..., T | Awaitable[T]]` argument, and this is exercised by
        # test_tui_repl.py's ToolCallLimitScreen tests).
        callback = self._confirm_tool_call_limit
        confirmed: bool = self.call_from_thread(callback, message)  # type: ignore[arg-type]
        return confirmed

    def _enter_interaction_mode(self) -> Vertical:
        """Disable and visually mute/collapse the prompt input while an interaction panel is
        active, returning the `#interaction-panel` container for the caller to mount that
        panel's content into."""
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.disabled = True
        prompt_input.add_class("interaction-active")
        return self.query_one(f"#{INTERACTION_PANEL_ID}", Vertical)

    def _exit_interaction_mode(self) -> None:
        """Un-mute and un-collapse the prompt input once an interaction panel is dismissed and
        re-enable it so the user can queue messages while the turn continues."""
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.remove_class("interaction-active")
        self._update_prompt_input_disabled_state()
        if not prompt_input.disabled:
            prompt_input.focus()

    def _record_interaction_history(self, header_text: str, body: str, decision_text: str) -> None:
        """Leave a permanent record of a just-finished permission ask or ask-user-questions
        exchange in the history scroll."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        records = (
            Static(INTERACTION_RECORD_LABEL, classes="interaction-record-label"),
            Static(header_text, classes="interaction-record-body", markup=False),
            Static(body, classes="interaction-record-body", markup=False),
            Static(f"Decision: {decision_text}", classes="interaction-record-decision", markup=False),
        )
        anchor = self._running_tool_call_anchor()
        if anchor is not None:
            history.mount(*records, before=anchor)
        else:
            history.mount(*records)
        self._scroll_if_pinned(history, was_pinned)

    def _register_interaction_future(
        self, future: "asyncio.Future[_InteractionResult]", teardown_default: _InteractionResult,
    ) -> Callable[[_InteractionResult], None]:
        """Wire a just-created interaction panel's decision `future` for safe resolution, returning
        the guarded `on_dismiss` callback the panel reports its result through."""
        def resolve(result: _InteractionResult) -> None:
            if not future.done():
                future.set_result(result)
        self._release_pending_interaction = lambda: resolve(teardown_default)
        return resolve

    @asynccontextmanager
    async def _reserved_interaction_slot(self, session_id: str) -> AsyncIterator[None]:
        """Acquire `_interaction_lock` for `session_id`'s interaction panel, re-verifying that
        `session_id` is still selected once the lock is actually granted."""
        while True:
            await self._await_session_selected(session_id)
            await self._interaction_lock.acquire()
            if self._selected_session.id == session_id:
                break
            self._interaction_lock.release()
        try:
            yield
        finally:
            self._interaction_lock.release()

    async def _confirm_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        """Mount a `PermissionAskPanel` for `ask_ctx` into `#interaction-panel` and wait for the
        user's choice."""
        session_id = ask_ctx.origin_session_id or self._session.id
        await self._await_session_selected(session_id)

        # Offload to a worker thread: `resolve_item_risk_assessment` makes a blocking, potentially
        # multi-second HTTP call to the risk-classifier model. Running it inline here would freeze
        # this event-loop thread for the duration, which both hangs the UI and starves the
        # main-thread timer that snoozes the liveness watchdog; a slow (but not wedged) classifier
        # response would then trip a false force-exit. Awaiting it off-thread keeps the loop
        # servicing its snooze timer throughout.
        risk_assessment = await asyncio.to_thread(
            resolve_item_risk_assessment,
            ask_ctx, session=self._session, process_config=self._process_config)

        grant_patterns: list[list[str]] | None = None
        if isinstance(ask_ctx.resource, CommandResource) and risk_assessment is not None and (
                risk_assessment.suggested_pattern):
            granted_preview: GrantPreview | None = GrantPreview(
                resource_text=" ".join(risk_assessment.suggested_pattern))
            grant_patterns = [risk_assessment.suggested_pattern]
        else:
            granted_preview = ask_ctx.resource.grant_preview(self._session.config)

        decision_future: asyncio.Future[PermissionDecision] = asyncio.get_running_loop().create_future()
        preview_wrap_width = max(
            _MIN_COMMAND_PREVIEW_WRAP_WIDTH, self.size.width - _COMMAND_PREVIEW_WIDTH_PADDING)
        initial_action = self._last_permission_action
        initial_scope = self._last_permission_scope
        if (
            risk_assessment is not None
            and risk_assessment.risk_score >= self._process_config.bash_risk_classifier_too_risky_threshold
        ):
            initial_action, initial_scope = "deny", "once"
        panel = PermissionAskPanel(
            ask_ctx, granted_preview=granted_preview, grant_patterns=grant_patterns,
            initial_action=initial_action, initial_scope=initial_scope,
            risk_score=risk_assessment.risk_score if risk_assessment is not None else None,
            risk_rationale=risk_assessment.rationale if risk_assessment is not None else None,
            preview_wrap_width=preview_wrap_width,
            on_dismiss=self._register_interaction_future(
                decision_future, PermissionDecision(action="deny", scope="once")))

        try:
            async with self._reserved_interaction_slot(session_id):
                panel_container = self._enter_interaction_mode()
                await panel_container.mount(panel)
                decision = await decision_future
                await panel.remove()
                self._exit_interaction_mode()
        finally:
            self._release_pending_interaction = None

        self._last_permission_action = decision.action
        self._last_permission_scope = decision.scope
        record_decision_history(
            ask_ctx, decision, session=self._session, process_config=self._process_config)
        # TODO(aaron): once a structured audit log for permission decisions exists, record an
        # entry here pairing `ask_ctx` (this command/path being asked about) with the user's own
        # `decision` -- this is the "this command _____ got this decision: _____" injection
        # point (a separate concern from pairing a command with its own risk assessment, whose
        # injection point is in klorb.permissions.risk_classifier.classify_command_risk).
        self._record_interaction_history(
            panel.header_text(), format_ask_context_body(ask_ctx), format_permission_decision(decision))
        return decision

    def _on_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        """Block the worker thread running `Session.send_turn()` until the user answers
        `PermissionAskPanel`, then return the decision as-is."""
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_permission_ask
        decision: PermissionDecision = self.call_from_thread(callback, ask_ctx)  # type: ignore[arg-type]
        return decision

    async def _confirm_ask_user_questions(
        self, ask_ctx: AskUserQuestionsItemContext,
    ) -> AskUserQuestionsAnswer:
        """Mount an `AskUserQuestionsPanel` for one question into `#interaction-panel` and wait
        for the user's answer."""
        session_id = ask_ctx.origin_session_id or self._session.id
        await self._await_session_selected(session_id)

        answer_future: asyncio.Future[AskUserQuestionsAnswer] = asyncio.get_running_loop().create_future()
        panel = AskUserQuestionsPanel(
            ask_ctx, on_dismiss=self._register_interaction_future(
                answer_future, AskUserQuestionsAnswer(cancelled=True)))

        try:
            async with self._reserved_interaction_slot(session_id):
                panel_container = self._enter_interaction_mode()
                await panel_container.mount(panel)
                answer = await answer_future
                await panel.remove()
                self._exit_interaction_mode()
        finally:
            self._release_pending_interaction = None

        self._record_interaction_history(
            panel.header_text(), ask_ctx.question, format_ask_user_questions_answer(answer))
        return answer

    def _on_ask_user_questions(self, ask_ctx: AskUserQuestionsItemContext) -> AskUserQuestionsAnswer:
        """Block the worker thread running `Session.send_turn()` until the user answers
        `AskUserQuestionsPanel` for this one question, then return the answer as-is."""
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_ask_user_questions
        answer: AskUserQuestionsAnswer = self.call_from_thread(callback, ask_ctx)  # type: ignore[arg-type]
        return answer

    async def _confirm_escalate_privileges(
        self, escalate_ctx: EscalatePrivilegesContext,
    ) -> EscalatePrivilegesDecision:
        """Mount an `EscalatePrivilegesPanel` for `escalate_ctx` into `#interaction-panel` and
        wait for the user's choice."""
        session_id = escalate_ctx.origin_session_id or self._session.id
        await self._await_session_selected(session_id)

        decision_future: asyncio.Future[EscalatePrivilegesDecision] = (
            asyncio.get_running_loop().create_future())
        panel = EscalatePrivilegesPanel(
            escalate_ctx, on_dismiss=self._register_interaction_future(
                decision_future, EscalatePrivilegesDecision(approved=False)))

        try:
            async with self._reserved_interaction_slot(session_id):
                panel_container = self._enter_interaction_mode()
                await panel_container.mount(panel)
                decision = await decision_future
                await panel.remove()
                self._exit_interaction_mode()
        finally:
            self._release_pending_interaction = None

        self._record_interaction_history(
            panel.header_text(), escalate_ctx.description,
            format_escalate_privileges_decision(decision))
        return decision

    def _on_escalate_privileges(
        self, escalate_ctx: EscalatePrivilegesContext,
    ) -> EscalatePrivilegesDecision:
        """Block the worker thread running `Session.send_turn()` until the user answers
        `EscalatePrivilegesPanel`, then return the decision as-is."""
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_escalate_privileges
        decision: EscalatePrivilegesDecision = self.call_from_thread(
            callback, escalate_ctx)  # type: ignore[arg-type]
        return decision
