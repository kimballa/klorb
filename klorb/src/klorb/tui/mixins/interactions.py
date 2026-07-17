# © Copyright 2026 Aaron Kimball
"""InteractionsMixin: the permission-ask, ask-user-questions, and escalate-privileges
confirmation flows for ReplApp."""

import asyncio
from collections.abc import Callable
from typing import TypeVar

from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from klorb.permissions.command_grant import compute_command_grant_patterns
from klorb.permissions.grant import compute_grant_paths
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
"""Horizontal space `PermissionAskPanel`'s own `padding: 1 2` (2 columns each side) consumes
around its command preview — subtracted from the app's terminal width to estimate the preview's
actual rendered width for `PermissionAskPanel`'s `preview_wrap_width` (see
`InteractionsMixin._confirm_permission_ask` and
`klorb.tui.panels.permission_ask_panel._command_preview`)."""
_MIN_COMMAND_PREVIEW_WRAP_WIDTH = 20
"""Floor for the wrap-width estimate above, so a very narrow terminal still gets a usable
(if aggressively wrapped) preview rather than a degenerate near-zero width."""

_InteractionResult = TypeVar("_InteractionResult")
"""The decision/answer type an interaction panel resolves its future with — see
`InteractionsMixin._register_interaction_future`."""


class InteractionsMixin(ReplAppBase):
    """Permission-ask/ask-user-questions/escalate-privileges confirm flows, plus the
    tool-call-limit confirmation and shared interaction-panel lifecycle helpers -- see
    `ReplApp` for how this mixes into the concrete app class."""

    async def _confirm_tool_call_limit(self, message: str) -> bool:
        """Show `ToolCallLimitScreen` with `message` and wait for the user's yes/no answer.

        Must be run on the app's own event loop, since it awaits the screen's dismissal —
        `_on_tool_call_limit_reached` is what the worker thread actually calls, via
        `call_from_thread`, to get here.
        """
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
        """Disable and visually mute/collapse the prompt input while an interaction panel (a
        permission ask or an ask-user-questions prompt) is active, returning the
        `#interaction-panel` container for the caller to mount that panel's content into.

        Collapsing to `height: 1` (via the `interaction-active` CSS class — see `ReplApp.CSS`)
        shrinks any in-progress multi-line draft back down to its default single-row size
        rather than leaving it competing with the panel for vertical space; the draft text
        itself is untouched underneath, so `_exit_interaction_mode` restoring `height: auto`
        brings it back exactly as the user left it.
        """
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.disabled = True
        prompt_input.add_class("interaction-active")
        return self.query_one(f"#{INTERACTION_PANEL_ID}", Vertical)

    def _exit_interaction_mode(self) -> None:
        """Un-mute and un-collapse the prompt input once an interaction panel is dismissed, but
        leave it *disabled*: an interaction panel only ever appears part-way through a turn that
        is still running (a permission ask, ask-user-questions, or escalate-privileges callback
        fired from inside `Session.send_turn`), so the input must stay locked until the whole turn
        finishes and `_finish_turn` re-enables it. Re-enabling here instead would let the user
        submit a second prompt mid-turn, launching a second concurrent `_send_prompt` worker whose
        own tool calls would mount interaction panels into `#interaction-panel` alongside this
        turn's — the "stacked panels" bug this guards against. Focus is intentionally not moved
        onto the (still-disabled) input either; the removed panel's focus is reassigned by Textual
        on its own."""
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.remove_class("interaction-active")

    def _record_interaction_history(self, header_text: str, body: str, decision_text: str) -> None:
        """Leave a permanent record of a just-finished permission ask or ask-user-questions
        exchange in the history scroll: `header_text` (e.g. `"Permission requested: Run
        command"` or the question's own `"Question 2 of 3 · Auth"` header), `body` (the
        command/path/detail or question text the panel showed), and `decision_text` (the user's
        rendered choice) — so scrolling back through the session shows not just that an
        approval happened, but what was asked and what was decided. Constructed with
        `markup=False`: `body` can be arbitrary command text or file paths, which must render
        verbatim.

        The record is floated *above* the `<Tool use>` block of the tool call that triggered
        the ask (see `_running_tool_call_anchor`), rather than appended below its `Running…`
        indicator: an ask is raised from inside a call's `apply()`, so the running-tool widget
        is already mounted by the time we get here, and reading top-to-bottom the decision
        belongs before the call it authorized, not after it. When no tool call is running (no
        anchor), the record is simply appended at the end as usual.
        """
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
        the guarded `on_dismiss` callback the panel reports its result through.

        The returned resolver is idempotent: a second dismiss (e.g. a stray keypress landing
        between the panel resolving and `_confirm_*` unmounting it), or a teardown resolving the
        future first, is a no-op rather than an `asyncio.InvalidStateError`. It also registers
        `_release_pending_interaction` so a teardown/abort path can resolve this same future with
        `teardown_default` (deny / cancelled), unblocking the worker thread parked in
        `App.call_from_thread` awaiting the decision and freeing `_interaction_lock`. The caller
        must clear `_release_pending_interaction` in its own `finally` once the await completes."""
        def resolve(result: _InteractionResult) -> None:
            if not future.done():
                future.set_result(result)
        self._release_pending_interaction = lambda: resolve(teardown_default)
        return resolve

    async def _confirm_permission_ask(self, ask_ctx: PermissionAskContext) -> PermissionDecision:
        """Mount a `PermissionAskPanel` for `ask_ctx` into `#interaction-panel` and wait for the
        user's choice.

        Must be run on the app's own event loop, since it awaits the panel's dismissal —
        `_on_permission_ask` is what the worker thread actually calls, via `call_from_thread`,
        to get here. `compute_grant_paths()`/`compute_command_grant_patterns()` are recomputed
        here (rather than threaded in from wherever `ask_ctx` was built) purely so the panel can
        show the directory/command pattern a persistent grant would actually cover; both are
        pure and read-only, so calling them again inside `_on_permission_ask`/
        `apply_permission_grant`/`apply_command_permission_grant` afterwards is safe and cheap.
        Seeds the panel's grid cursor from `_last_permission_action`/`_last_permission_scope`
        and updates both from the returned decision, so a run of several asks for one compound
        tool call starts each next prompt where the previous one left off — unless
        `klorb.permissions.risk_classifier.resolve_item_risk_assessment` rates this item at or
        above `tools.bash.riskClassifier.tooRiskyThreshold`, which pre-selects `Deny, once`
        instead (still just a starting cursor position; every cell stays reachable and
        confirmable regardless). `ReplApp` itself never constructs an `ItemRiskAssessment` or
        talks to the classifier directly — `resolve_item_risk_assessment` owns the gating,
        batching (across a compound command's several serially-asked items), and caching, so
        this same call would work identically from any other UI layer driving `Session`. Once the
        user's own `PermissionDecision` comes back, `klorb.permissions.risk_classifier.
        record_decision_history` records it (command text plus decision) into this `session`'s
        bounded history, so a later `resolve_item_risk_assessment` call this session can use it as
        calibration context — see that function's own docstring.
        """
        # Offload to a worker thread: `resolve_item_risk_assessment` makes a blocking, potentially
        # multi-second HTTP call to the risk-classifier model. Running it inline here would freeze
        # this event-loop thread for the duration, which both hangs the UI and starves the
        # main-thread timer that snoozes the liveness watchdog -- a slow (but not wedged) classifier
        # response would then trip a false force-exit. Awaiting it off-thread keeps the loop
        # servicing its snooze timer throughout. See docs/specs/interrupt-and-liveness-watchdog.md.
        risk_assessment = await asyncio.to_thread(
            resolve_item_risk_assessment,
            ask_ctx, session=self._session, process_config=self._process_config)

        granted_paths = None
        granted_command_patterns = None
        if ask_ctx.path is not None:
            granted_paths = compute_grant_paths(
                self._session.config.read_dirs, self._session.config.write_dirs,
                self._session.config.workspace.path, ask_ctx.path, ask_ctx.is_write)
        elif ask_ctx.command is not None:
            if risk_assessment is not None and risk_assessment.suggested_pattern:
                granted_command_patterns = [risk_assessment.suggested_pattern]
            else:
                granted_command_patterns = compute_command_grant_patterns(
                    self._session.config.command_rules, ask_ctx.command)

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
            ask_ctx, granted_paths=granted_paths, granted_command_patterns=granted_command_patterns,
            initial_action=initial_action, initial_scope=initial_scope,
            risk_score=risk_assessment.risk_score if risk_assessment is not None else None,
            risk_rationale=risk_assessment.rationale if risk_assessment is not None else None,
            preview_wrap_width=preview_wrap_width,
            on_dismiss=self._register_interaction_future(
                decision_future, PermissionDecision(action="deny", scope="once")))

        try:
            async with self._interaction_lock:
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
        """`Session`'s `on_permission_ask` callback: block the worker thread running
        `Session.send_turn()` until the user answers `PermissionAskPanel`, then return that
        choice as-is. Applying (and, for "workspace"/"homedir", persisting to disk) any grant
        the choice implies is `Session`'s own responsibility —
        `Session._retry_after_permission_decision`, via
        `klorb.permissions.grant.apply_permission_grant` — using the `ProcessConfig` reference
        `ReplApp` gave it at construction time; this callback only needs to surface the user's
        decision, not act on it.
        """
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_permission_ask
        decision: PermissionDecision = self.call_from_thread(callback, ask_ctx)  # type: ignore[arg-type]
        return decision

    async def _confirm_ask_user_questions(
        self, ask_ctx: AskUserQuestionsItemContext,
    ) -> AskUserQuestionsAnswer:
        """Mount an `AskUserQuestionsPanel` for one question into `#interaction-panel` and wait
        for the user's answer.

        Must be run on the app's own event loop, since it awaits the panel's dismissal —
        `_on_ask_user_questions` is what the worker thread actually calls, via
        `call_from_thread`, to get here.
        """
        answer_future: asyncio.Future[AskUserQuestionsAnswer] = asyncio.get_running_loop().create_future()
        panel = AskUserQuestionsPanel(
            ask_ctx, on_dismiss=self._register_interaction_future(
                answer_future, AskUserQuestionsAnswer(cancelled=True)))

        try:
            async with self._interaction_lock:
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
        """`Session`'s `on_ask_user_questions` callback: block the worker thread running
        `Session.send_turn()` until the user answers `AskUserQuestionsPanel` for this one
        question, then return that answer as-is — `Session._resolve_ask_user_questions` calls
        this once per question in an `AskUserQuestionsRequired` batch and assembles the
        results itself.
        """
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_ask_user_questions
        answer: AskUserQuestionsAnswer = self.call_from_thread(callback, ask_ctx)  # type: ignore[arg-type]
        return answer

    async def _confirm_escalate_privileges(
        self, escalate_ctx: EscalatePrivilegesContext,
    ) -> EscalatePrivilegesDecision:
        """Mount an `EscalatePrivilegesPanel` for `escalate_ctx` into `#interaction-panel` and
        wait for the user's choice.

        Must be run on the app's own event loop, since it awaits the panel's dismissal \u2014
        `_on_escalate_privileges` is what the worker thread actually calls, via
        `call_from_thread`, to get here.
        """
        decision_future: asyncio.Future[EscalatePrivilegesDecision] = (
            asyncio.get_running_loop().create_future())
        panel = EscalatePrivilegesPanel(
            escalate_ctx, on_dismiss=self._register_interaction_future(
                decision_future, EscalatePrivilegesDecision(approved=False)))

        try:
            async with self._interaction_lock:
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
        """`Session`'s `on_escalate_privileges` callback: block the worker thread running
        `Session.send_turn()` until the user answers `EscalatePrivilegesPanel`, then return
        that decision as-is — `Session._resolve_escalate_privileges` records the approved
        scope into `SessionConfig.approved_scopes` itself; this callback only surfaces the
        user's decision, not act on it.
        """
        # See the type-ignore note on `_on_tool_call_limit_reached` above; same mypy limitation.
        callback = self._confirm_escalate_privileges
        decision: EscalatePrivilegesDecision = self.call_from_thread(
            callback, escalate_ctx)  # type: ignore[arg-type]
        return decision
