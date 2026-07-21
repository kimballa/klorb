# © Copyright 2026 Aaron Kimball
"""`SessionPermissionsMixin`: resolves the ask-style exceptions a tool call can raise
(`PermissionAskRequired`/`MultiPermissionAskRequired`, `AskUserQuestionsRequired`,
`EscalatePrivilegesRequired`) into a `(result, error)` pair for `_run_tool_calls`, and confirms
whether to raise a tool-call safety limit that was just reached."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from klorb.message import ToolCallRequest
from klorb.paths import KLORB_CONFIG_DIR, KLORB_DATA_DIR, KLORB_STATE_DIR
from klorb.permissions.resource import PermissionOverride
from klorb.permissions.table import MultiPermissionAskRequired, PermissionAskItem, PermissionAskRequired
from klorb.session.events import (
    AskUserQuestionsItemContext,
    EscalatePrivilegesContext,
    PermissionAskContext,
    PermissionDecision,
    TurnEventHandlers,
)
from klorb.session.mixins._base import SessionBase
from klorb.tools.ask.common import AskUserQuestionsRequired
from klorb.tools.escalate_privileges.common import EscalatePrivilegesRequired

if TYPE_CHECKING:
    # `GrantAction`/`GrantScope` are simple `Literal` aliases living in `klorb.permissions.grant`,
    # which imports `SessionConfig` from `klorb.session.config` for real — so a type-checking-only
    # import avoids the cycle while still letting `_apply_ask_grant` annotate its parameters.
    from klorb.permissions.grant import GrantAction, GrantScope

logger = logging.getLogger(__name__)


class SessionPermissionsMixin(SessionBase):
    """Ask-exception resolution (permission asks, `AskUserQuestions`, `EscalatePrivileges`) and
    tool-call safety-limit confirmation -- see `klorb.session.mixins.tool_execution` for the
    dispatch loop that calls into these."""

    def _confirm_limit_increase(
        self,
        scope: Literal["turn", "session"],
        current_count: int,
        current_limit: int,
        on_tool_call_limit_reached: Callable[[str], bool] | None,
    ) -> bool:
        """A `max_tool_calls_per_{scope}` limit was just reached. Ask (via
        `on_tool_call_limit_reached`, if given) whether to double it and keep going.

        Returns `False` without asking anything if `on_tool_call_limit_reached` is `None`,
        since there's no way to interactively confirm outside e.g. the TUI (see
        [[terminal-repl]]'s `ToolCallLimitScreen`) — a one-shot CLI prompt has nobody to ask.
        Otherwise calls it with a human-readable prompt describing the limit reached, and:
        on `True`, doubles `self.config.max_tool_calls_per_{scope}` for the rest of this
        `Session`'s lifetime (not just this turn) and returns `True`; on `False`, returns
        `False` without changing the limit.
        """
        logger.warning(
            "%s tool-call limit reached (%d/%d).", scope.capitalize(), current_count, current_limit)
        if on_tool_call_limit_reached is None:
            return False
        prompt = (
            f"This task has made {current_count} tool calls this {scope}, reaching its "
            f"configured limit of {current_limit}. Continue tool use?"
        )
        if not on_tool_call_limit_reached(prompt):
            logger.info("User declined to raise the %s tool-call limit; aborting turn.", scope)
            return False
        new_limit = current_limit * 2
        if scope == "turn":
            self.config.max_tool_calls_per_turn = new_limit
        else:
            self.config.max_tool_calls_per_session = new_limit
        logger.info("User approved continuing; %s tool-call limit doubled to %d.", scope, new_limit)
        return True

    def _retry_after_permission_decision(
        self,
        call: ToolCallRequest,
        args: dict[str, Any],
        ask_exc: PermissionAskRequired,
        decision: PermissionDecision,
    ) -> tuple[Any, str | None]:
        """Resolve a `PermissionAskRequired` into a `(result, error)` pair (see
        `_format_tool_response_content` for how a caller turns this into `tool_response`
        content): for a persistent `scope`, first applies the grant `decision` implies via
        `ask_exc.resource.apply_grant()` — passing `self.config` and `self._process_config`
        (possibly `None`; `apply_grant()` skips the process-wide ripple, but still persists to
        disk, in that case) — then retries the call exactly once, same as
        `scope="once"` (which instead retries via a one-shot `ToolSetupContext.
        permission_override`, persisting nothing — only meaningful for `action="allow"`; a
        `"once"` deny needs no override at all). Reports a denial for `action="deny"` (any
        scope) or `other_text` with no retry. Any exception from the retried call (including a
        second `PermissionAskRequired`) is reported the same generic way an ordinary tool
        failure is — never a second ask.

        Handles every resource kind `ask_exc.resource` might be: the persistent-scope grant is
        dispatched by `_apply_ask_grant`, and a `scope="once"` retry carries the resource on the
        matching `PermissionOverride` field, via `ask_exc.resource.added_to_override()`.
        """
        assert ask_exc.resource.is_persistable
        if decision.other_text is not None:
            return None, f"Permission denied: {ask_exc}. User note: {decision.other_text}"
        if decision.action == "deny":
            if decision.scope != "once":
                self._apply_ask_grant("deny", decision.scope, ask_exc)
            return None, f"Permission denied: {ask_exc}"
        if decision.scope in ("session", "workspace", "homedir"):
            self._apply_ask_grant("allow", decision.scope, ask_exc)
        assert self._tool_registry is not None
        try:
            if decision.scope == "once":
                override = ask_exc.resource.added_to_override(PermissionOverride())
                tool = self._tool_registry.instantiate_tool(call.name, permission_override=override)
            else:
                tool = self._tool_registry.instantiate_tool(call.name)
            return tool.apply(args), None
        except Exception as exc:
            logger.warning("Retried tool call %s(%s) failed: %s", call.name, call.arguments, exc)
            return None, str(exc)

    def _apply_ask_grant(
        self,
        action: "GrantAction",
        scope: "GrantScope",
        ask_exc: PermissionAskRequired,
    ) -> None:
        """Persist a single-item permission grant for `ask_exc` at `scope`, via
        `ask_exc.resource.apply_grant()`."""
        ask_exc.resource.apply_grant(action, scope, self.config, self._process_config)

    def _retry_after_multi_permission_decisions(
        self,
        call: ToolCallRequest,
        args: dict[str, Any],
        items: list[PermissionAskItem],
        decisions: list[PermissionDecision],
    ) -> tuple[Any, str | None]:
        """Resolve a `MultiPermissionAskRequired`'s items into a `(result, error)` pair, given
        one `PermissionDecision` already collected per item (`_run_tool_calls` stops collecting
        — and calls this with a shorter `decisions` list than `items` — at the first `"deny"`
        answer; see that method). Applies every persistent-scope `"allow"`/`"deny"` decision's
        grant via that item's own `resource.apply_grant()` (a no-op for a structural item, which
        has no rule to persist). Every `scope="once"` decision instead contributes its item to a
        single `PermissionOverride` (see `ToolSetupContext.permission_override`), via
        `resource.added_to_override()`, covering all of them at once, since retrying happens
        exactly once for the whole call — never once per item, as re-parsing/re-evaluating after
        each individual grant would be wasted work. If `decisions` is shorter than `items` (an
        item was denied), or any collected decision is itself a denial, reports that denial with
        no retry at all.
        """
        for item, decision in zip(items, decisions):
            if decision.action == "deny" or decision.other_text is not None:
                reason = f"Permission denied: {item.resource_description}"
                if decision.other_text is not None:
                    reason += f". User note: {decision.other_text}"
                return None, reason

        if len(decisions) < len(items):
            denied_item = items[len(decisions)]
            return None, f"Permission denied: {denied_item.resource_description}"

        override: PermissionOverride | None = None
        for item, decision in zip(items, decisions):
            if decision.scope == "once":
                override = item.resource.added_to_override(override or PermissionOverride())
                continue
            item.resource.apply_grant(
                decision.action, decision.scope, self.config, self._process_config,
                grant_patterns=decision.grant_patterns)

        assert self._tool_registry is not None
        try:
            tool = self._tool_registry.instantiate_tool(call.name, permission_override=override)
            return tool.apply(args), None
        except Exception as exc:
            logger.warning("Retried tool call %s(%s) failed: %s", call.name, call.arguments, exc)
            return None, str(exc)

    def _resolve_multi_permission_ask(
        self,
        call: ToolCallRequest,
        args: dict[str, Any],
        multi_ask_exc: MultiPermissionAskRequired,
        callbacks: TurnEventHandlers,
    ) -> tuple[Any, str | None]:
        """Resolve a `MultiPermissionAskRequired` into a `(result, error)` pair, branching on
        `config.permission_framework` exactly like the single-item `PermissionAskRequired` path
        does — except a non-persistable (structural) item is never automatically failed closed
        here (see `MultiPermissionAskRequired`'s own docstring for why). `"ask"` asks about every
        item in order via `callbacks.on_permission_ask`, stopping at the first `action="deny"` (or
        `other_text`-bearing) answer — the remaining items are never asked about, and
        `_retry_after_multi_permission_decisions` reports that denial without retrying.
        """
        if self.config.permission_framework == "deny":
            logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, multi_ask_exc)
            logger.warning("User configured us to auto-reject approval request.")
            return None, (str(multi_ask_exc) +
                "\nThis kind of request is automatically denied. Do not repeat it.")
        if self.config.permission_framework == "auto":
            logger.info(
                "Auto-approving permission ask under permissionFramework=auto: %s", multi_ask_exc)
            auto_decisions = [
                PermissionDecision(action="allow", scope="session") for _ in multi_ask_exc.items]
            return self._retry_after_multi_permission_decisions(
                call, args, multi_ask_exc.items, auto_decisions)
        if callbacks.on_permission_ask is None:
            logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, multi_ask_exc)
            logger.warning("User unavailable to respond to approval request.")
            return None, (str(multi_ask_exc) +
                "\nThe user is unavailable to approve this kind of request. Do not repeat it.")

        decisions: list[PermissionDecision] = []
        for item in multi_ask_exc.items:
            decision = callbacks.on_permission_ask(PermissionAskContext(
                resource=item.resource, bash_context=item.bash_context,
                resource_description=item.resource_description, sibling_items=multi_ask_exc.items))
            decisions.append(decision)
            if decision.action == "deny" or decision.other_text is not None:
                break
        return self._retry_after_multi_permission_decisions(call, args, multi_ask_exc.items, decisions)

    def _resolve_ask_user_questions(
        self,
        call: ToolCallRequest,
        ask_exc: AskUserQuestionsRequired,
        callbacks: TurnEventHandlers,
    ) -> tuple[Any, str | None]:
        """Resolve an `AskUserQuestionsRequired` into a `(result, error)` pair: asks
        `callbacks.on_ask_user_questions` about each of `ask_exc.questions` in turn, building
        `result["answers"]` from the collected `AskUserQuestionsAnswer`s. Unlike a permission
        ask, there is no `config.permission_framework` branching (asking the user isn't a
        resource-access verdict an "auto"/"deny" framework applies to — see
        `docs/adrs/ask-user-questions-tool-bypasses-permission-tables.md`) and no "retry
        `tool.apply()`" step afterward: asking the question was this tool's entire job, so the
        collected answers directly become the result.

        With no callback given (e.g. a headless one-shot run — see `_send_and_receive`'s
        prompt-path callers), fails closed the same as an unhandled `PermissionAskRequired`
        would, since there is nobody to ask. A `cancelled` answer for any question
        short-circuits the rest of the batch: the call fails, naming the cancelled question and
        echoing back any answers already collected for earlier questions in the same call, so
        that information isn't silently lost.
        """
        if callbacks.on_ask_user_questions is None:
            logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, ask_exc)
            return None, str(ask_exc)

        total = len(ask_exc.questions)
        answers: list[dict[str, Any]] = []
        for index, question in enumerate(ask_exc.questions):
            answer = callbacks.on_ask_user_questions(AskUserQuestionsItemContext(
                header=question.header, question=question.question, options=question.options,
                index=index, total=total))
            if answer.cancelled:
                collected = ", ".join(a["header"] for a in answers) or "none"
                return None, (
                    f"User declined to answer question {index + 1}/{total} "
                    f"('{question.header}': {question.question}). Answers already collected "
                    f"for earlier questions in this call: {collected}. Don't keep guessing or "
                    "re-asking the same thing a different way -- reconsider your approach, or "
                    "ask a clearer, narrower question."
                )
            answers.append({
                "header": question.header,
                "question": question.question,
                "answer": answer.answer,
            })
        return {"answers": answers}, None

    def _resolve_escalate_privileges(
        self,
        call: ToolCallRequest,
        escalate_exc: EscalatePrivilegesRequired,
        callbacks: TurnEventHandlers,
    ) -> tuple[Any, str | None]:
        """Resolve an `EscalatePrivilegesRequired` into a `(result, error)` pair: asks
        `callbacks.on_escalate_privileges` for an `EscalatePrivilegesDecision` and, on
        approval, records the scope into `SessionConfig.approved_scopes` (`self.config`,
        the in-memory, session-scoped set `is_privileged_path` consults — see
        `klorb.permissions.directory_access.privileged_dirs`). Like `_resolve_ask_user_questions`,
        there is no `permission_framework` branching (escalation isn't a resource-access verdict
        the auto/deny framework applies to) and no retry afterward: recording the approval was
        this tool's entire job, so the decision directly becomes the result.

        With no callback given (e.g. a headless one-shot run — see `_send_and_receive`'s
        prompt-path callers), fails closed: the privileged-path deny stays in effect and the
        tool reports the denial back to the model. The recording target is `self.config`
        (the session's own `SessionConfig`), which always exists, so — unlike when
        `approved_scopes` lived on `ProcessConfig` — a `Session` constructed without a
        `ProcessConfig` no longer fails closed here: escalation is session-scoped, and the
        grant is recorded on the session itself.
        """
        if callbacks.on_escalate_privileges is None:
            logger.warning("Tool call %s(%s) failed: %s", call.name, call.arguments, escalate_exc)
            return None, (
                f"Escalation of '{escalate_exc.scope}' scope was not approved: there is no "
                "interactive surface to ask through, so the privileged-path deny stays in "
                "effect. Read or write the file through a non-privileged path instead, or ask "
                "the user to run klorb interactively so they can approve this escalation."
            )

        scope = escalate_exc.scope
        description = (
            f"Grant Klorb read/write access to the workspace's .klorb/ directory "
            f"({self.config.workspace.path}/.klorb/) for the rest of this session."
        ) if scope == "workspace" else (
            f"Grant Klorb read/write access to {KLORB_DATA_DIR}, {KLORB_STATE_DIR}, "
            f"and {KLORB_CONFIG_DIR} for the rest of this session."
        ) if scope == "homedir" else str(escalate_exc)
        decision = callbacks.on_escalate_privileges(EscalatePrivilegesContext(
            scope=scope, description=description))
        if decision.approved:
            self.config.approved_scopes.add(scope)
            logger.info("Escalation approved for scope '%s'", scope)
            return {"scope": scope, "approved": True}, None
        logger.info("Escalation denied for scope '%s'", scope)
        return None, f"Escalation of '{scope}' scope was denied by the user."
