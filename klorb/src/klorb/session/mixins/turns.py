# © Copyright 2026 Aaron Kimball
"""`SessionTurnsMixin`: the turn-dispatch loop -- streams one request/response round trip
(`_send_and_receive`), drives it through as many tool-call rounds as the model asks for
(`_dispatch_turn`), and the three public entry points (`send_turn`, `retry_last_turn`,
`run_one_shot`) that build or resend the user-facing `Message` each turn revolves around."""

import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from klorb.api_provider import ProviderResponse, ResponseAborted
from klorb.message import Message
from klorb.session.constants import MAX_TOOL_CALL_ROUNDS, ToolCallLimitExceeded
from klorb.session.events import TurnEventHandlers
from klorb.session.mixins._base import SessionBase
from klorb.token_estimate import estimate_tokens

logger = logging.getLogger(__name__)

_SKILL_MENTION_RE = re.compile(r"(?:^|\s)/([A-Za-z0-9][A-Za-z0-9._:-]*)")
"""Matches a `/<token>` slug, where `token` may itself be a colon-qualified fully-qualified skill
name (`<namespace>:<name>`, see `klorb.permissions.skill_access.format_fqsn`) -- a skill name can
never contain a colon (`klorb.tools.skill.common.is_valid_skill_name`), so a colon inside the
captured token is unambiguously the fqsn separator, not part of the name."""

_LEADING_SKILL_RE = re.compile(r"^\s*/([A-Za-z0-9][A-Za-z0-9._:-]*)")
"""Matches a `/<token>` (see `_SKILL_MENTION_RE`) at the very start of a prompt, ignoring leading
whitespace -- used to detect a message that *starts* with a skill reference, which
`Session.send_turn()` treats as an unconditional activation rather than a casual mention (see
`_build_user_skill_activation_interjection` and docs/specs/skills.md)."""


def _skill_mention_tokens(prompt: str) -> list[str]:
    """Every distinct `/<token>` slug mentioned in `prompt`, in first-seen order. A cheap,
    disk-free candidate extraction `Session._build_skill_reference_interjection` runs before
    deciding whether a skill-discovery scan is even worth doing for this turn."""
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _SKILL_MENTION_RE.finditer(prompt):
        token = match.group(1)
        if token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def _leading_skill_token(prompt: str) -> str | None:
    """The `/<token>` slug at the very start of `prompt` (ignoring leading whitespace), or `None`
    if the prompt doesn't start with one."""
    match = _LEADING_SKILL_RE.match(prompt)
    return match.group(1) if match else None


_SKILL_WORD_RE = re.compile(r"(?<![a-zA-Z0-9])skills?\b|/skills?\b", re.IGNORECASE)


def _prompt_mentions_skill(prompt: str) -> bool:
    """Return ``True`` if ``prompt`` contains the word "skill" or "/skill" (case-insensitive).

    Used by `Session.send_turn()` to decide whether to prepend the ``SkillReminder``
    system interjection — a lightweight hint that suggests the agent look for relevant
    skills via `SearchSkills` before proceeding.
    """
    return bool(_SKILL_WORD_RE.search(prompt))


def _wrap_system_interjection(subject: str, message: str) -> str:
    """Wrap `message` in a `<SystemInterjection subject="{subject}">...</SystemInterjection>`
    tag pair, so a model can tell an out-of-band harness notice apart from the user's own
    turn content within the same `Message.content` — see `Session.send_turn()`'s handling of
    `_pending_permission_framework_interjection`."""
    return f'<SystemInterjection subject="{subject}">\n{message}\n</SystemInterjection>'


class SessionTurnsMixin(SessionBase):
    """The turn-dispatch loop and its three public entry points: `send_turn`, `retry_last_turn`,
    `run_one_shot`."""

    def _send_and_receive(
        self,
        message_snapshot: list[Message],
        system_prompt: str | None,
        model_name: str,
        reasoning: dict[str, Any] | None,
        drop_reasoning: bool,
        tools: list[dict[str, Any]] | None,
        callbacks: TurnEventHandlers,
    ) -> tuple[Message, ProviderResponse]:
        """Send one request to the active model and return `(reply, result)`.

        Streams the reply: on the first chunk, a placeholder `Message`
        (`processing_state="started_receipt"`, `streaming_content=[]`) is appended to
        `self._messages`, and each chunk's text is appended to it. On success, that same
        placeholder is finalized in place (`content`, `streaming_content=None`,
        `processing_state="complete"`, `finish_reason`) rather than appending a second,
        duplicate `Message`; if no placeholder was ever created (e.g. a non-streaming test
        double), the provider's reply is appended fresh instead. The finalized message's
        `role` is `"tool_use"` (with `tool_calls` populated) if the model asked to call
        tools, or `"assistant"` otherwise.

        Reasoning/thinking deltas are handled the same way, but as a separate
        `role="thinking"` placeholder appended ahead of the reply one, so the two can be told
        apart and rendered separately; a structured `reasoning_details` payload (see
        `Message.reasoning_details`), if the provider sends one, is attached to that same
        placeholder (lazily creating it too, if reasoning arrived as only structured details
        with no plain-text delta ever streamed).

        Every message's `num_tokens` is a client-side `tiktoken` count of its own content
        (`klorb.token_estimate.estimate_tokens`) -- refreshed on every chunk while streaming,
        so `Session.total_tokens_used()` has a live, steadily-accurate number throughout the
        round trip, and left as-is once a message stops changing. See
        docs/adrs/count-every-message-tokens-client-side-with-tiktoken.md.

        On any exception other than `ResponseAborted`, whichever placeholder(s) were created
        are mutated to `processing_state="error"`/`last_error` (partial `streaming_content`
        left intact) before re-raising, mirroring how the caller is expected to mark the
        turn's own anchor message (a user message, or a prior round's `tool_response`). On
        `ResponseAborted`, those placeholder(s) are instead finalized in place with
        `processing_state="aborted"` and whatever `streaming_content` had arrived before the
        interruption folded into `content` — the user may still want to read a partial reply,
        and it stays out of the way of a resent prompt since `"aborted"` isn't `"complete"`.
        """
        placeholder: Message | None = None
        thinking_placeholder: Message | None = None

        def ensure_thinking_placeholder() -> Message:
            nonlocal thinking_placeholder
            if thinking_placeholder is None:
                thinking_placeholder = Message(
                    content="",
                    role="thinking",
                    num_tokens=0,
                    processing_state="started_receipt",
                    timestamp=datetime.now(),
                    streaming_content=[],
                )
                self._messages.append(thinking_placeholder)
            return thinking_placeholder

        def handle_chunk(delta_text: str) -> None:
            nonlocal placeholder
            if placeholder is None:
                placeholder = Message(
                    content="",
                    role="assistant",
                    num_tokens=0,
                    processing_state="started_receipt",
                    timestamp=datetime.now(),
                    streaming_content=[],
                )
                self._messages.append(placeholder)
            assert placeholder.streaming_content is not None
            placeholder.streaming_content.append(delta_text)
            placeholder.num_tokens = estimate_tokens("".join(placeholder.streaming_content))
            if callbacks.on_chunk is not None:
                callbacks.on_chunk(delta_text)

        def handle_thinking_chunk(delta_text: str) -> None:
            thinking = ensure_thinking_placeholder()
            assert thinking.streaming_content is not None
            thinking.streaming_content.append(delta_text)
            thinking.num_tokens = estimate_tokens("".join(thinking.streaming_content))
            if callbacks.on_thinking_chunk is not None:
                callbacks.on_thinking_chunk(delta_text)

        def handle_reasoning_details_chunk(accumulated: list[dict[str, Any]]) -> None:
            thinking = ensure_thinking_placeholder()
            thinking.reasoning_details = accumulated
            if callbacks.on_reasoning_details is not None:
                callbacks.on_reasoning_details(accumulated)

        try:
            result = self._provider.send_prompt(
                message_snapshot, system_prompt=system_prompt, model=model_name,
                session_id=self.id, reasoning=reasoning, tools=tools,
                drop_reasoning=drop_reasoning, on_chunk=handle_chunk,
                on_thinking_chunk=handle_thinking_chunk,
                on_reasoning_details=handle_reasoning_details_chunk,
                cache_mgmt_style=self._cache_mgmt_style(),
                cancel_event=callbacks.cancel_event)
        except ResponseAborted:
            if thinking_placeholder is not None:
                thinking_placeholder.content = "".join(thinking_placeholder.streaming_content or [])
                thinking_placeholder.streaming_content = None
                thinking_placeholder.processing_state = "aborted"
            if placeholder is not None:
                placeholder.content = "".join(placeholder.streaming_content or [])
                placeholder.streaming_content = None
                placeholder.processing_state = "aborted"
            raise
        except Exception as exc:
            total_chars = sum(len(m.content) for m in message_snapshot)
            logger.error(
                "API call failed for model=%s messages=%d"
                " total_chars=%d: %s",
                model_name, len(message_snapshot), total_chars, exc,
            )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("Conversation history:")
                for i, m in enumerate(message_snapshot):
                    logger.debug(
                        "msg idx=%s:\n%s",
                        i,
                        m.model_dump_json(
                            indent=2, exclude_unset=True,
                            exclude_defaults=True,
                        ),
                    )

            if placeholder is not None:
                placeholder.processing_state = "error"
                placeholder.last_error = str(exc)
            if thinking_placeholder is not None:
                thinking_placeholder.processing_state = "error"
                thinking_placeholder.last_error = str(exc)
            raise

        if thinking_placeholder is not None:
            thinking_placeholder.content = "".join(thinking_placeholder.streaming_content or [])
            thinking_placeholder.streaming_content = None
            thinking_placeholder.processing_state = "complete"
            thinking_placeholder.last_error = None
            self.statistics.thinking_messages += 1

        if placeholder is not None:
            placeholder.content = result.message.content
            placeholder.streaming_content = None
            placeholder.processing_state = "complete"
            placeholder.last_error = None
            placeholder.num_tokens = result.message.num_tokens
            placeholder.finish_reason = result.message.finish_reason
            placeholder.tool_calls = result.message.tool_calls
        else:
            placeholder = result.message
            self._messages.append(placeholder)

        if placeholder.tool_calls:
            placeholder.role = "tool_use"
        self.statistics.response_messages += 1
        self.statistics.record_usage(
            input_tokens=result.prompt_tokens,
            output_tokens=result.completion_tokens,
            cached_tokens=result.cached_tokens,
            cost=result.total_cost,
        )

        return placeholder, result

    def _dispatch_turn(
        self,
        user_message: Message,
        callbacks: TurnEventHandlers | None = None,
    ) -> str:
        """Send `user_message` (already present in `self._messages`) and mutate it in place.

        Delegates the actual request/response streaming to `_send_and_receive()`. If the
        model's reply requests tool calls (`reply.role == "tool_use"`), `_run_tool_calls()`
        dispatches them via `tool_registry` and appends their results as `tool_response`
        messages, then `_send_and_receive()` is called again with the updated history — this
        repeats until the model returns a plain `"assistant"` reply, or `ToolCallLimitExceeded`
        is raised after `MAX_TOOL_CALL_ROUNDS` round trips, `max_tool_calls_per_turn`
        individual tool calls in this turn, or `max_tool_calls_per_session` individual tool
        calls across this `Session`'s lifetime (see `_run_tool_calls`).
        `self._tool_calls_this_turn` is reset to `0` at the start of every call to
        `_dispatch_turn` (including retries); `self._tool_calls_this_session` is never reset.

        `user_message.num_tokens` is already set (from `estimate_tokens(user_message.content)`
        at construction, see `send_turn`/`retry_last_turn`) and untouched here. On success,
        `processing_state` becomes `"complete"` and `last_error` is cleared. On failure,
        `user_message` is mutated to `processing_state="error"` with `last_error` set (the
        failing round's own placeholder(s) are already marked the same way by
        `_send_and_receive`), and the exception is re-raised.

        If `callbacks.cancel_event` is given and the provider raises `ResponseAborted` (because
        it was set mid-stream), `user_message` and every message already appended for this
        turn — any earlier round's completed `tool_use`/`tool_response` messages (their tool
        calls already ran, with real side effects, and stay in history exactly like a
        completed turn's would), plus the aborted round's placeholder(s), already finalized
        with `processing_state="aborted"` by `_send_and_receive` — are left in
        `self._messages` rather than erased. `user_message.processing_state` becomes
        `"aborted"` too. The exception is re-raised so the caller can report the interruption.
        """
        callbacks = callbacks or TurnEventHandlers()
        self._ensure_system_message()
        self._ensure_tool_defs_message()
        self._tool_calls_this_turn = 0
        model_name = self.active_model_name()
        system_prompt = self._resolve_system_prompt()
        reasoning = self._reasoning_params()
        drop_reasoning = self._drop_reasoning()
        tools = self._tool_registry.tool_definitions() if self._tool_registry is not None else None

        self.active_cancel_event = callbacks.cancel_event
        try:
            try:
                reply, _ = self._send_and_receive(
                    list(self._messages), system_prompt, model_name, reasoning, drop_reasoning,
                    tools, callbacks)

                rounds = 0
                while reply.role == "tool_use":
                    if callbacks.cancel_event is not None and callbacks.cancel_event.is_set():
                        # Abort at the round boundary too, not just mid-stream: a turn that was
                        # cancelled (Escape, or an interactive quit -- see
                        # klorb.tui.ReplApp._begin_exit) while a tool call was running, or while
                        # its worker thread was parked awaiting a permission decision, would otherwise
                        # keep issuing more rounds/streams before the provider's own mid-stream
                        # cancel_event check (klorb.openrouter.send_prompt) got another chance to fire.
                        raise ResponseAborted()
                    rounds += 1
                    if rounds > MAX_TOOL_CALL_ROUNDS:
                        logger.warning(
                            "Tool-call round limit reached (%d/%d) for %s; aborting turn.",
                            rounds - 1, MAX_TOOL_CALL_ROUNDS, model_name)
                        raise ToolCallLimitExceeded(
                            f"Exceeded {MAX_TOOL_CALL_ROUNDS} tool-call round trips in one turn.")
                    logger.info(
                        "Turn tool-call round %d/%d for %s", rounds, MAX_TOOL_CALL_ROUNDS, model_name)
                    self._run_tool_calls(reply, callbacks)
                    reply, _ = self._send_and_receive(
                        list(self._messages), system_prompt, model_name, reasoning, drop_reasoning,
                        tools, callbacks)
            except ResponseAborted:
                user_message.processing_state = "aborted"
                logger.info("Turn aborted for %s", model_name)
                raise
            except Exception as exc:
                user_message.processing_state = "error"
                user_message.last_error = str(exc)
                total_chars = sum(len(m.content) for m in self._messages)
                logger.error(
                    "Turn failed for %s: %s (messages=%d, total_chars=%d)",
                    model_name, exc, len(self._messages), total_chars,
                )
                logger.error("Full exception details:", exc_info=True)
                raise

            user_message.processing_state = "complete"
            user_message.last_error = None
            logger.debug(
                "Turn complete for %s: user num_tokens=%d, assistant num_tokens=%d, finish_reason=%s",
                model_name, user_message.num_tokens, reply.num_tokens, reply.finish_reason,
            )
            return reply.content
        finally:
            self.active_cancel_event = None

    def send_turn(
        self,
        prompt: str,
        callbacks: TurnEventHandlers | None = None,
    ) -> str:
        """Send one turn of the conversation to the active model and return its response.

        Appends a new user `Message` to the session's history, sends the full history (plus
        the session's resolved system prompt — see `_resolve_system_prompt`) to the
        provider, and mutates that
        same `Message` in place to reflect the outcome (see `_dispatch_turn`). `callbacks`
        (a `TurnEventHandlers`, defaulting to one with every field `None` if omitted) bundles
        every optional hook for this turn: `on_chunk`/`on_thinking_chunk`/`on_reasoning_details`
        are invoked with each text/reasoning delta (and reasoning payload update) as the
        response streams in; if `cancel_event` is set while the
        response is streaming in, the turn is aborted: `ResponseAborted` is raised, and the
        user `Message` appended here — along with whatever partial reply/thinking content and
        completed tool-call rounds accumulated before the interruption — stays in history with
        `processing_state="aborted"` rather than being removed (see `_dispatch_turn`);
        `on_tool_call_limit_reached` is invoked (with a human-readable prompt) when a tool-call
        safety limit is reached, to ask whether to double it and keep going — see
        `_confirm_limit_increase`; `on_permission_ask` is invoked when a tool call hits an
        `"ask"` permission verdict, to ask whether (and how broadly) to allow it — see
        `_run_tool_calls`.

        If a permission-framework change is pending (`set_permission_framework()` was called
        since the last turn), the queued interjection is wrapped in a `<SystemInterjection
        subject="PermissionFramework">` tag (see `_wrap_system_interjection`) and prepended
        onto `prompt` before it's stored as the user `Message`'s content — see
        docs/specs/permissions.md's "Permission framework change interjection" section — and
        the pending state is cleared, so it's applied exactly once. After that, every provider
        registered via `register_standing_interjection()` is polled (in a fixed, deterministic
        order — sorted by subject) and any non-`None` result is prepended the same way, but not
        cleared: a standing interjection keeps appearing on every subsequent turn for as long as
        its provider keeps returning a message.

        When the user's own `prompt` *starts* with a skill reference (ignoring leading
        whitespace) that resolves to an `"allow"`-verdicted skill, a `UserSkillActivation`
        interjection carrying that skill's full `ActivateSkill`-equivalent JSON payload is
        prepended immediately ahead of the original prompt text -- an unconditional activation,
        not a mere reminder (see `_build_user_skill_activation_interjection`). A `SkillReference`
        interjection is separately prepended for this turn when the prompt mentions any other
        discoverable skill by `/<name>` (or `/<namespace>:<name>`), excluding whichever skill the
        leading-mention activation already fully handled. On the first turn only, the one-shot
        `AvailableSkills` catalog is also prepended (see `_build_skill_reference_interjection`/
        `_build_available_skills_interjection` and docs/specs/skills.md).

        Finally, the very first time `send_turn()` is ever called on this `Session`
        (`self._context_files_seeded` still `False`), `_build_context_files_interjection()` is
        consulted once; a non-`None` result is wrapped in a `<SystemInterjection
        subject="ProjectGuidance">` tag and prepended last, so it ends up as the outermost —
        i.e. first-read — preamble to the whole prompt, ahead of the `PermissionFramework`/
        standing/skill interjections above. `self._context_files_seeded` is then set
        unconditionally, so this never runs again for the rest of the `Session`'s lifetime,
        matching every other one-shot interjection's "fires once" contract — see
        docs/specs/workspace-context-files.md.

        On the first turn only, a one-shot `metadata` interjection carrying the session
        start time and workspace root name is also prepended (see `self._metadata_seeded`).

        Also on the first turn only (`self._session_naming_pending`), the session-naming
        classifier runs against `prompt` (before any interjection is prepended) via
        `_run_session_naming`, which renames `id`/`root_id` (via `set_id`) and sets `name` on
        success. This runs identically whether `send_turn()` is driven by the interactive TUI or
        a headless one-shot call (see `run_one_shot`); `callbacks.on_session_name_changed`, if
        given, is invoked once with the result (or `None` on failure) so a caller can react --
        e.g. the TUI updates its status line and renames its session log file.
        """
        original_prompt = prompt
        self._ensure_skill_catalog()
        excluded_skill_ids: frozenset[tuple[str, str]] = frozenset()
        leading_token = _leading_skill_token(original_prompt)
        if leading_token is not None:
            activation = self._build_user_skill_activation_interjection(leading_token)
            if activation is not None:
                prompt = f"{_wrap_system_interjection('UserSkillActivation', activation.body)}\n{prompt}"
                excluded_skill_ids = frozenset({activation.skill_id})
        if self._pending_permission_framework_interjection is not None:
            interjection = _wrap_system_interjection(
                "PermissionFramework", self._pending_permission_framework_interjection)
            prompt = f"{interjection}\n{prompt}"
            self._pending_permission_framework_interjection = None
        for subject in sorted(self._standing_interjection_providers):
            message = self._standing_interjection_providers[subject]()
            if message is not None:
                prompt = f"{_wrap_system_interjection(subject, message)}\n{prompt}"
        skill_mention_tokens = _skill_mention_tokens(original_prompt)
        skill_reference = self._build_skill_reference_interjection(
            skill_mention_tokens, exclude=excluded_skill_ids)
        if skill_reference is not None:
            prompt = f"{_wrap_system_interjection('SkillReference', skill_reference)}\n{prompt}"
        if _prompt_mentions_skill(original_prompt):
            skill_reminder = (
                "The user's message mentions the word \"skill\" or \"/skill\". Consider whether "
                "a relevant skill exists that should be loaded first — use SearchSkills to look "
                "for matching skills, then ActivateSkill to load one before proceeding."
            )
            prompt = f"{_wrap_system_interjection('SkillReminder', skill_reminder)}\n{prompt}"
        if not self._skills_seeded:
            self._skills_seeded = True
            available_skills = self._build_available_skills_interjection(self._discover_skills())
            if available_skills is not None:
                prompt = f"{_wrap_system_interjection('AvailableSkills', available_skills)}\n{prompt}"
        if not self._context_files_seeded:
            self._context_files_seeded = True
            project_guidance = self._build_context_files_interjection()
            if project_guidance is not None:
                prompt = f"{_wrap_system_interjection('ProjectGuidance', project_guidance)}\n{prompt}"
        if not self._metadata_seeded:
            self._metadata_seeded = True
            started_at = self._session_started_at.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
            workspace_root = str(self.config.workspace.path)
            metadata_body = (
                f"The session began at {started_at}.\n"
                f"The workspace root is `{workspace_root}`."
            )
            prompt = f"{_wrap_system_interjection('metadata', metadata_body)}\n{prompt}"
        if self._session_naming_pending:
            self._session_naming_pending = False
            naming_result = self._run_session_naming(original_prompt)
            if callbacks is not None and callbacks.on_session_name_changed is not None:
                callbacks.on_session_name_changed(naming_result)
        user_message = Message(
            content=prompt,
            role="user",
            num_tokens=estimate_tokens(prompt),
            processing_state="pending",
            timestamp=datetime.now(),
        )
        self._messages.append(user_message)
        self.statistics.user_messages += 1
        logger.info(
            "Sending turn to %s (%d messages in context)", self.active_model_name(), len(self._messages))
        return self._dispatch_turn(user_message, callbacks)

    def retry_last_turn(
        self,
        callbacks: TurnEventHandlers | None = None,
    ) -> str:
        """Resend the most recently errored user turn's content, mutating that same `Message`.

        Scans from the end of the history for the last user-role `Message`; if none exists
        or it isn't in `"error"` state, raises `ValueError`. Any messages after it (e.g. a
        partial assistant placeholder left behind by a failed stream) are discarded before
        resending, since they belong to the failed attempt, not the conversation. `callbacks`
        is the same `TurnEventHandlers` bundle `send_turn()` takes, including
        `callbacks.cancel_event` — a retried turn can be aborted mid-stream exactly like an
        original one.
        """
        index = len(self._messages) - 1
        while index >= 0 and self._messages[index].role != "user":
            index -= 1
        if index < 0 or self._messages[index].processing_state != "error":
            raise ValueError("No errored turn to retry.")

        del self._messages[index + 1:]
        errored_message = self._messages[index]
        errored_message.processing_state = "pending"
        logger.info("Retrying errored turn for %s", self.active_model_name())
        return self._dispatch_turn(errored_message, callbacks)

    def run_one_shot(
        self,
        prompt: str,
        on_chunk: Callable[[str], None] | None = None,
        on_thinking_chunk: Callable[[str], None] | None = None,
    ) -> str:
        """Run a single, non-interactive turn and return the model's text response.

        Passes no `on_permission_ask`/`on_tool_call_limit_reached`/`on_ask_user_questions`
        callbacks — there's no interactive surface to ask through here — so any `"ask"`
        permission verdict resolves per `config.permission_framework` (see
        `SessionConfig.permission_framework`), hitting a tool-call limit always raises
        `ToolCallLimitExceeded`, and an `AskUserQuestions` tool call always fails closed (there
        is nobody to ask). `send_turn()` already
        loops through every tool-call round on its own until the model returns a final
        plain-text reply, so this call runs the whole task to completion, not just one
        model round trip. No `on_session_name_changed` is passed, so this call's `Session` still
        gets named (renaming `id`/`root_id`/`name`) on its first invocation like any other
        `send_turn()` caller, just with nothing reacting to the result.
        """
        return self.send_turn(
            prompt, TurnEventHandlers(on_chunk=on_chunk, on_thinking_chunk=on_thinking_chunk))
