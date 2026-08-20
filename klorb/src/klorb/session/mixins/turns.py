# © Copyright 2026 Aaron Kimball
"""`SessionTurnsMixin`: the turn-dispatch loop."""

import base64
import logging
import re
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from klorb.api_provider import ProviderResponse, ResponseAborted
from klorb.images.prepare import extension_for_mime_type
from klorb.message import Message, MessageFragment
from klorb.session.constants import (
    MAX_TOOL_CALL_ROUNDS,
    ChainedHookMessageUndeliverableError,
    ToolCallLimitExceeded,
)
from klorb.session.events import QueuedMessage, TurnEventHandlers
from klorb.session.mixins._base import SessionBase
from klorb.session.mixins.mentions import resolve_at_mentions
from klorb.token_estimate import estimate_message_tokens, estimate_tokens
from klorb.workspace.session_store import write_session_image

if TYPE_CHECKING:
    # `klorb.session` (this package's own `__init__.py`) assembles `Session` from this mixin, so
    # importing it for real here would be circular; needed only to `cast` `self` to `Session`
    # below, matching `klorb.session.mixins.core`'s own use of this pattern.
    from klorb.session import Session

logger = logging.getLogger(__name__)

_SKILL_MENTION_RE = re.compile(r"(?:^|\s)/([A-Za-z0-9][A-Za-z0-9._:-]*)")
"""Matches a `/<token>` slug, where `token` may be a colon-qualified fully-qualified skill name."""

_LEADING_SKILL_RE = re.compile(r"^\s*/([A-Za-z0-9][A-Za-z0-9._:-]*)")
"""Matches a `/<token>` at the very start of a prompt, ignoring leading whitespace."""


def _skill_mention_tokens(prompt: str) -> list[str]:
    """Every distinct `/<token>` slug mentioned in `prompt`, in first-seen order."""
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
    """Return ``True`` if ``prompt`` contains the word "skill" or "/skill" (case-insensitive)."""
    return bool(_SKILL_WORD_RE.search(prompt))


class HookDeniedTurnError(Exception):
    """Raised when an `onSubmitUserPrompt` hook's aggregate `HookOutput.success` is `False`,
    refusing to send this turn to the model at all."""


def wrap_system_interjection(subject: str, message: str) -> str:
    """Wrap `message` in a `<SystemInterjection subject="{subject}">...</SystemInterjection>`
    tag pair, so a model can tell an out-of-band harness notice apart from the user's own
    turn content within the same `Message.content`."""
    return f'<SystemInterjection subject="{subject}">\n{message}\n</SystemInterjection>'


def _image_header_text(index: int, image_fragment: MessageFragment) -> str:
    """Text-fragment header prepended immediately ahead of each image fragment. Names the
    image's 1-indexed position among the ones attached to this turn, plus its origin."""
    origin = (
        f"filename='{image_fragment.source_filename}'" if image_fragment.source_filename
        else "(pasted from clipboard)")
    return f"The following is image #{index} in the order provided by the user: {origin}"


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

        Streams the reply: on the first chunk, a placeholder `Message` is appended to
        `self._messages`, and each chunk's text is appended to it. On success, that same
        placeholder is finalized in place rather than appending a second, duplicate
        `Message`. The finalized message's `role` is `"tool_use"` if the model asked to call
        tools, or `"assistant"` otherwise.

        Reasoning/thinking deltas are handled the same way, but as a separate
        `role="thinking"` placeholder appended ahead of the reply one, so the two can be told
        apart and rendered separately.

        Every message's `num_tokens` is a client-side `tiktoken` count, refreshed on every
        chunk while streaming.

        On any exception other than `ResponseAborted`, placeholder(s) are mutated to
        `processing_state="error"` before re-raising. On `ResponseAborted`, they are
        finalized with `processing_state="aborted"` and whatever `streaming_content` had
        arrived is folded into `content`.
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
                cancel_event=callbacks.cancel_event, max_tokens=self._max_output_tokens)
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
        model's reply requests tool calls, `_run_tool_calls()` dispatches them and
        appends their results as `tool_response` messages, then `_send_and_receive()` is
        called again with the updated history — this repeats until the model returns a plain
        `"assistant"` reply, or `ToolCallLimitExceeded` is raised.
        `self._tool_calls_this_turn` is reset to `0` at the start of every call.

        On success, `processing_state` becomes `"complete"` and `last_error` is cleared. On
        failure, `user_message` is mutated to `processing_state="error"` with `last_error`
        set, and the exception is re-raised. On `ResponseAborted`, `user_message` becomes
        `"aborted"` too.

        `_chained_hook_turns` resets to `0` here unconditionally, unless
        `_chain_continuation_pending` says otherwise.
        """
        callbacks = callbacks or TurnEventHandlers()
        self._ensure_system_message()
        self._ensure_tool_defs_message()
        self._tool_calls_this_turn = 0
        if self._chain_continuation_pending:
            self._chain_continuation_pending = False
        else:
            self._chained_hook_turns = 0
        model_name = self.active_model_name()
        system_prompt = self._resolve_system_prompt()
        reasoning = self._reasoning_params()
        drop_reasoning = self._drop_reasoning()
        tools = self._tool_registry.tool_definitions() if self._tool_registry is not None else None

        self.active_cancel_event = callbacks.cancel_event
        self._current_turn_handlers = callbacks
        try:
            try:
                self._apply_submit_user_prompt_hook(user_message)
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
                    self.deliver_queued_user_message(callbacks)
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
            result_text = reply.content
        finally:
            self.active_cancel_event = None
            self._current_turn_handlers = None
        self._fire_agent_turn_end_hook(result_text)
        return result_text

    def _apply_submit_user_prompt_hook(self, user_message: Message) -> None:
        """Dispatch `onSubmitUserPrompt` for this turn's `user_message`, before
        it's sent to the model -- for any session's own turn, root or subagent. A `message` in
        the aggregate `HookOutput` rewrites `user_message.content` in place;
        `user_message.fragments` are left untouched. `success=False` raises
        `HookDeniedTurnError`."""

        result = self._dispatch_hook("onSubmitUserPrompt", message=user_message.content)
        if result.success is False:
            raise HookDeniedTurnError(
                result.message or "Turn blocked by onSubmitUserPrompt hook policy.")
        if result.message is not None and result.message != user_message.content:
            user_message.content = result.message
            user_message.num_tokens = estimate_tokens(user_message.content)

    def _fire_agent_turn_end_hook(self, reply_text: str) -> None:
        """Dispatch `onAgentTurnEnd` once this session's own turn has fully ended -- for any
        session, root or subagent.

        A `reset_session` result calls `Session.reset_session()` and delivers `result.message`
        as the reset conversation's first turn -- first dispatching `onSessionEnd` with
        `reason="ResetSession"` too, but only for a root session: `onSessionEnd` never applies to
        a subagent, reset or otherwise.
        """
        result = self._dispatch_hook("onAgentTurnEnd", message=reply_text)
        if result.reset_session:
            assert result.message is not None
            if self.parent is None:
                self._dispatch_lifecycle_hook("onSessionEnd", reason="ResetSession")
            cast("Session", self).reset_session()
            self._deliver_chained_hook_message(result.message)
            return
        if result.message is not None:
            self._deliver_chained_hook_message(result.message)

    def deliver_event_message(self, text: str) -> None:
        """Deliver an event handler's aggregate `message` into this session's conversation.

        If a turn is already running, `text` is queued for that turn's host to pick up
        once it ends. Otherwise `text` is queued and `deliver_wake()` pings the registered
        wake handler -- the same live-host mechanism a root session's TUI/ACP/headless front
        door uses. A dormant subagent (no turn running, no wake handler -- its ordinary
        between-turns state) has no such host at all: `_deliver_event_to_dormant_subagent`
        starts a fresh turn directly instead."""
        if self.current_turn_handlers() is not None:
            self.enqueue_queued_message(QueuedMessage(message_text=text, origin="event"))
            return
        if self._wake_handler is not None:
            self.enqueue_queued_message(QueuedMessage(message_text=text, origin="event"))
            self.deliver_wake()
            return
        if self.parent is not None:
            self._deliver_event_to_dormant_subagent(text)
            return
        raise ChainedHookMessageUndeliverableError(
            f"Event delivered to idle session {self.id} with no live host to show it to: {text!r}")

    def _deliver_event_to_dormant_subagent(self, text: str) -> None:
        """A dormant subagent has no live host to queue an event message into the way a root
        session's TUI/ACP/headless host does -- start a fresh turn directly instead, exactly
        like `klorb.agents.policy.dispatch_direct_message`'s dormant-child branch does for a
        human-sent message, except silently skipped (never raising, never delivered) if doing so
        would exceed `tools.subagents.maxConcurrentPerParent`/`maxActiveTotal`. The resulting
        turn's output is not relayed to the parent's own conversation (`parent_interested=False`)
        -- the parent didn't ask for this turn, so it stays out of the parent's context until the
        parent explicitly calls `WaitForSubagent`/`MessageSubagent`."""
        from klorb.agents.policy import concurrency_limits_exceeded, dispatch_subagent_turn
        assert self.parent is not None
        assert self._process_config is not None
        tracker = self.parent.subagent_tracker
        with tracker.dispatch_guard():
            current = tracker.current_handle(self.id)
            if current is None:
                return
            if current.state == "running":
                # Raced between the caller's earlier idle-check and this lock -- queue like an
                # ordinary in-flight event instead of starting a second, overlapping turn.
                self.enqueue_queued_message(QueuedMessage(message_text=text, origin="event"))
                return
            if concurrency_limits_exceeded(self._process_config, self.parent):
                logger.info(
                    "Event delivered to dormant subagent %s skipped: would exceed subagent "
                    "concurrency limits.", self.id)
                return
            dispatch_subagent_turn(
                self.parent, cast("Session", self), current.role, current.title, text,
                parent_interested=False)

    def _spill_image_fragment_to_disk(self, image_fragment: MessageFragment) -> MessageFragment:
        """Write `image_fragment`'s in-memory bytes to `sessions/<subdir>/images/` and return the
        same fragment with `image_path` populated. A no-op if this session's directory isn't
        claimed."""
        if self._session_subdir is None:
            return image_fragment
        assert image_fragment.image_url is not None
        assert image_fragment.mime_type is not None
        data = base64.b64decode(image_fragment.image_url["url"].split(",", 1)[1])
        image_fragment.image_path = write_session_image(
            self.config.workspace, self._session_subdir, data,
            extension_for_mime_type(image_fragment.mime_type))
        return image_fragment

    def send_turn(
        self,
        prompt: str,
        callbacks: TurnEventHandlers | None = None,
        image_fragments: list[MessageFragment] | None = None,
        resolve_mentions: bool = True,
    ) -> str:
        """Send one turn of the conversation to the active model and return its response.

        Appends a new user `Message` from `prompt`, resolves `@mention`ed files into
        `MessageFragment`s, prepends any pending interjections (permission framework,
        standing providers, skill activations, context files, memories, metadata), and
        dispatches via `_dispatch_turn`. `image_fragments`, if given, are appended after
        the prompt fragment.

        On the first turn only, the session directory is claimed and the session-naming
        classifier is kicked off.
        """
        original_prompt = prompt
        active_model = self.active_model()
        mention_fragments = resolve_at_mentions(
            original_prompt, self._mention_read_file_core, self.config.workspace.path,
            active_model=active_model, image_pipeline_config=self._image_pipeline_config,
        ) if resolve_mentions else None
        self._ensure_skill_catalog()
        typed_catalog = self._skill_catalog_registry.typed()
        skill_mention_tokens = _skill_mention_tokens(original_prompt)
        self._current_turn_mentioned_skill_ids = frozenset(
            (skill.namespace, skill.name)
            for skill in map(typed_catalog.resolve_reference, skill_mention_tokens)
            if skill is not None
        )
        self._current_turn_leading_skill_id = None
        excluded_skill_ids: frozenset[tuple[str, str]] = frozenset()
        leading_token = _leading_skill_token(original_prompt)
        if leading_token is not None:
            leading_skill = typed_catalog.resolve_reference(leading_token)
            if leading_skill is not None:
                self._current_turn_leading_skill_id = (leading_skill.namespace, leading_skill.name)
                activation = self._build_user_skill_activation_interjection(leading_skill)
                if activation is not None:
                    prompt = (
                        f"{wrap_system_interjection('UserSkillActivation', activation.body)}\n{prompt}")
                    excluded_skill_ids = frozenset({activation.skill_id})
                    if callbacks is not None and callbacks.on_skill_activated is not None:
                        callbacks.on_skill_activated(activation.skill_id)
        if self._pending_permission_framework_interjection is not None:
            interjection = wrap_system_interjection(
                "PermissionFramework", self._pending_permission_framework_interjection)
            prompt = f"{interjection}\n{prompt}"
            self._pending_permission_framework_interjection = None
        for subject in sorted(self._standing_interjection_providers):
            message = self._standing_interjection_providers[subject]()
            if message is not None:
                prompt = f"{wrap_system_interjection(subject, message)}\n{prompt}"
        skill_reference = self._build_skill_reference_interjection(
            skill_mention_tokens, exclude=excluded_skill_ids)
        if skill_reference is not None:
            prompt = f"{wrap_system_interjection('SkillReference', skill_reference)}\n{prompt}"
        if _prompt_mentions_skill(original_prompt):
            skill_reminder = (
                "The user's message mentions the word \"skill\" or \"/skill\". Consider whether "
                "a relevant skill exists that should be loaded first — use SearchSkills to look "
                "for matching skills, then ActivateSkill to load one before proceeding."
            )
            prompt = f"{wrap_system_interjection('SkillReminder', skill_reminder)}\n{prompt}"
        if not self._skills_seeded:
            self._skills_seeded = True
            skills = self.discover_skills()
            available_skills = self._build_available_skills_interjection(skills)
            if available_skills is not None:
                prompt = f"{wrap_system_interjection('AvailableSkills', available_skills)}\n{prompt}"
                logger.info(
                    "Interjecting with available skills: %d skills, %d tokens",
                    len(skills),
                    estimate_tokens(available_skills),
                )
        if not self._context_files_seeded:
            self._context_files_seeded = True
            project_guidance = self._build_context_files_interjection()
            if project_guidance is not None:
                prompt = f"{wrap_system_interjection('ProjectGuidance', project_guidance)}\n{prompt}"
        if not self._memories_seeded:
            self._memories_seeded = True
            memories_body = self._build_memories_interjection()
            if memories_body is not None:
                prompt = f"{wrap_system_interjection('Memories', memories_body)}\n{prompt}"
        if not self._additional_tools_seeded:
            self._additional_tools_seeded = True
            additional_tools_body = self._build_additional_tools_interjection()
            if additional_tools_body is not None:
                prompt = f"{wrap_system_interjection('AdditionalTools', additional_tools_body)}\n{prompt}"
        if not self._metadata_seeded:
            self._metadata_seeded = True
            started_at = self._session_started_at.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
            workspace_root = str(self.config.workspace.path)

            workspace_git_path: Path = self.config.workspace.path / ".git"
            git_branch: str|None = None
            if workspace_git_path.exists():
                logger.debug("Found git repo for workspace at %s", workspace_git_path)
                git_branch_output: subprocess.CompletedProcess = subprocess.run(
                    ["git", "branch", "--show-current", "--no-color"], cwd=workspace_root,
                    timeout=5.0, capture_output=True, text=True)
                if git_branch_output.returncode == 0:
                    git_branch = str(git_branch_output.stdout).strip()
                    logger.info("Current git branch is %s", git_branch)
                else:
                    logger.error(".git subdir of workspace found at '%s' "
                                 "but git branch returncode=%s: stderr=%s",
                                 workspace_git_path, git_branch_output.returncode, git_branch_output.stderr)

            metadata_strs = [
                f"The session began at {started_at}. "
                f"The workspace root is `{workspace_root}`. "
            ]
            if git_branch:
                metadata_strs.append(f"The current git branch is `{git_branch}`. ")

            claude_skills_compat: bool = \
                self._process_config.compatibility_claude_skills if self._process_config else False
            metadata_strs.append(f"compatibility.claudeSkills = {claude_skills_compat}")

            metadata_body = "\n".join(metadata_strs)
            prompt = f"{wrap_system_interjection('Metadata', metadata_body)}\n{prompt}"
        if self._session_naming_pending:
            self._session_naming_pending = False
            # `self.id` is fixed for the session's lifetime, so claiming can happen right away
            # rather than waiting on the classifier's round trip -- and must happen *before*
            # `_start_session_naming` spawns its background thread, since that thread's own
            # end-of-run `persist_state()` call assumes claiming has already settled. A no-op if
            # already claimed (a restored session that
            # adopted its directory before this call) or the workspace is untrusted.
            self.claim_session_directory()
            self._start_session_naming(original_prompt, callbacks)
        fragments: list[MessageFragment] | None = None
        if mention_fragments is not None or image_fragments:
            fragments = []
            if mention_fragments is not None:
                for mention_fragment in mention_fragments:
                    if mention_fragment.type == "image_url":
                        mention_fragment = self._spill_image_fragment_to_disk(mention_fragment)
                    fragments.append(mention_fragment)
                logger.debug(
                    "Attaching %d @mention fragment(s) to user turn (plus the prompt fragment)",
                    len(mention_fragments),
                )
            fragments.append(MessageFragment(type="text", text=prompt))
            if image_fragments:
                for index, image_fragment in enumerate(image_fragments, start=1):
                    fragments.append(MessageFragment(type="text", text=_image_header_text(
                        index, image_fragment)))
                    fragments.append(self._spill_image_fragment_to_disk(image_fragment))
                logger.debug("Attaching %d image fragment(s) to user turn", len(image_fragments))
        user_message = Message(
            content=prompt,
            fragments=fragments,
            role="user",
            num_tokens=0,
            processing_state="pending",
            timestamp=datetime.now(),
        )
        user_message.num_tokens = (
            estimate_message_tokens(user_message, active_model) if active_model is not None
            else estimate_tokens(user_message.body()))
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

        Also drives this session's own drain-and-resubmit loop (mirroring the TUI's
        `_finish_turn`/ACP's `TurnBridge.run_turn`), the "host" a headless caller otherwise
        wouldn't have: once a turn ends, anything an `onAgentTurnEnd` `chat` handler queued via
        `_deliver_chained_hook_message` is drained and resubmitted as the next turn, repeating
        until nothing's left queued. Returns the *last* turn's response text.
        """
        handlers = TurnEventHandlers(on_chunk=on_chunk, on_thinking_chunk=on_thinking_chunk)
        response = self.send_turn(prompt, handlers)
        next_turn_text = self.drain_next_turn_text(handlers)
        while next_turn_text is not None:
            response = self.send_turn(next_turn_text, handlers)
            next_turn_text = self.drain_next_turn_text(handlers)
        return response
