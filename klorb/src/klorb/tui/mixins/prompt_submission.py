# © Copyright 2026 Aaron Kimball
"""PromptSubmissionMixin: submitting a prompt or shell command and driving a turn to
completion for ReplApp."""

import logging
import threading
from typing import Any

from textual import work
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Markdown, Static

from klorb.agents.policy import compute_root_session_grants, dispatch_direct_message
from klorb.api_provider import ResponseAborted
from klorb.logging_config import configure_logging, session_log_path
from klorb.process_config import apply_cli_flags_to_session, load_process_config
from klorb.session import Session, ToolCallEvent, ToolCallStartedEvent, TurnEventHandlers
from klorb.session.events import QueuedMessage
from klorb.session_naming import SessionName
from klorb.tools.exceptions import ToolCallError
from klorb.tui._base import ReplAppBase
from klorb.tui.constants import HISTORY_ID, NEW_SESSION_LABEL, PROMPT_INPUT_ID, SESSION_NAME_ID
from klorb.tui.formatting import summarize_reasoning_details
from klorb.tui.shell import ShellCommandCancelled, ShellCommandTimedOut, UserShellCommand
from klorb.tui.widgets.prompt_input import PromptInput

logger = logging.getLogger(__name__)


class TuiSessionWake(Message):
    """Posted by the active session's registered wake handler when a timer, filesystem, or
    trust-change event queues a message while no turn is in flight."""


class PromptSubmissionMixin(ReplAppBase):
    """Prompt submission, shell-command dispatch, and turn finalization."""

    def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        """Echo the submitted prompt into the history and dispatch it to the model, or
        handle `:q`/`/quit`/`/exit` or a `!`-prefixed shell command synchronously."""
        prompt_text = event.value.strip()
        if not prompt_text:
            return

        event.prompt_input.text = ""
        if prompt_text in [":q", "/quit", "/exit"]:
            self._begin_exit()
            return

        if prompt_text.startswith("!") and "\n" not in prompt_text and "\r" not in prompt_text:
            self._submit_shell_command(prompt_text[1:].lstrip())
            return

        if self._chat_selected:
            self._submit_chat_post(prompt_text)
            return

        if self._selected_handle is not None:
            self._submit_subagent_prompt(prompt_text)
            return

        if self._turn_in_flight:
            self._queue_prompt(prompt_text)
            return

        self._submit_prompt(prompt_text)

    def _submit_subagent_prompt(self, prompt_text: str) -> None:
        """Send `prompt_text` directly to the selected subagent, bypassing the parent agent."""
        assert self._selected_handle is not None
        try:
            dispatch_direct_message(
                self._process_config, self._selected_session, self._selected_handle, prompt_text)
        except ToolCallError as exc:
            self.show_notice(str(exc), error=True)

    def _submit_shell_command(self, command: str) -> None:
        """Echo `!command` into the history, disable the input, and dispatch it to a worker
        thread so a slow command can't block the UI."""
        if self._turn_in_flight:
            return
        self._turn_in_flight = True

        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(f"!{command}", classes="prompt", markup=False))
        history.scroll_end(animate=False)

        self._shell_cancel_event = threading.Event()
        self.refresh_bindings()
        self._run_shell_command(command, self._shell_cancel_event)

    def _queue_prompt(self, prompt_text: str) -> None:
        """Queue `prompt_text` for delivery once the current turn ends, and echo it into the
        history with a `<Queued message>` header and italic styling."""
        queued_msg = QueuedMessage(message_text=prompt_text)
        self._session.enqueue_queued_message(queued_msg)

    @work(thread=True)
    def _run_shell_command(self, command: str, cancel_event: threading.Event) -> None:
        """Run `command` via `UserShellCommand` on a worker thread, streaming its combined
        stdout/stderr into the history as it arrives."""
        output_widget: Static | None = None
        accumulated = ""
        output_lock = threading.Lock()

        def handle_output(delta_text: str) -> None:
            nonlocal accumulated, output_widget
            with output_lock:
                accumulated += delta_text
                if output_widget is None:
                    output_widget = self.call_from_thread(self._mount_shell_output_widget, accumulated)
                else:
                    self.call_from_thread(
                        self._update_shell_output_widget, output_widget, accumulated)

        error_message: str | None = None
        # Outer try/finally guarantees `_turn_in_flight` clears however this worker unwinds,
        # including a BaseException past the handlers below.
        try:
            try:
                _, _, rc = UserShellCommand(
                    command, shell_path=self._process_config.shell_command).run(
                        on_stdout=handle_output, on_stderr=handle_output,
                        timeout=self._process_config.shell_timeout_seconds, cancel_event=cancel_event)
            except ShellCommandTimedOut:
                timeout = self._process_config.shell_timeout_seconds
                error_message = f"Shell command timed out after {timeout:g}s; killed."
            except ShellCommandCancelled:
                error_message = "Shell command interrupted."
            else:
                if rc != 0:
                    error_message = f"Shell command exited with status {rc}."

            self.call_from_thread(self._finish_shell_command, error_message)
        finally:
            try:
                self.call_from_thread(self._ensure_turn_finished, cancel_event)
            except Exception:
                pass

    def _mount_shell_output_widget(self, initial_text: str) -> Static:
        """Mount a new `Static` widget for a streaming shell command's output and return it.

        Uses `Static` instead of `Markdown` because CommonMark collapses newlines within a
        paragraph into spaces, which mangles multi-line command output.
        `markup=False` prevents a literal `[` from being misread as a Textual markup tag.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Static(initial_text, markup=False)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _update_shell_output_widget(self, widget: Static, text: str) -> None:
        """Update a streaming shell command's output `Static` widget with the latest
        accumulated `text`."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)

    def _finish_shell_command(self, error_message: str | None) -> None:
        """Show `error_message` (if any) in the history, then finish the shell command's
        "turn": scroll to the end, refresh the token tally, and re-enable/refocus the
        input box.
        """
        if error_message is not None:
            self._show_error(error_message)
        else:
            history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
            self._finish_turn(history, self._history_pinned_to_bottom, agent_turn_succeeded=False)

    def clear_session(self) -> None:
        """Replace the active Session with a fresh, blank one."""
        self._replace_session(None)

    def rename_session(self, title: str) -> None:
        """Set the active session's display title and persist the change. Cancels the one-shot
        naming classifier (if still pending) so it doesn't overwrite the user's choice."""
        logger.debug("Renaming session title to %r", title)
        self._session.name = title
        self._session.session_naming_pending = False
        self._session.persist_state()
        self._update_session_name_line(title)

    def get_current_session_title(self) -> str:
        """Return the active session's current title, or an empty string if unnamed."""
        return self._session.name or ""

    def _replace_session(self, initial_message: str | None) -> None:
        """Replace the active `Session` with a fresh one and reset the visible history. If a
        turn is in flight, cancels it first (as if the user pressed Ctrl+C) and defers the
        actual replacement until `_finish_turn` observes that it has unwound."""
        if self._replacing_session:
            logger.warning(
                "Ignoring a session-replacement request that arrived while another one was "
                "already in progress.")
            return
        self._replacing_session = True
        try:
            if threading.get_ident() == self._thread_id:
                self._start_replace_session(initial_message)
            else:
                self.call_from_thread(self._start_replace_session, initial_message)
        finally:
            self._replacing_session = False

    def _start_replace_session(self, initial_message: str | None) -> None:
        """Replaces the session immediately if idle, or cancels the in-flight turn/shell command
        and defers the replacement to `_finish_turn`'s tail."""
        if self._turn_in_flight:
            self._pending_session_replacement = True
            self._pending_session_replacement_initial_message = initial_message
            self._release_workers_for_exit()
            return
        self._do_replace_session(initial_message)

    def _do_replace_session(self, initial_message: str | None) -> None:
        """Closes the active `Session`, builds a fresh one, and resets the visible history to
        it."""
        old_session: Session = self._session

        workspace = old_session.config.workspace
        with self._watchdog.suspended():
            old_session.close()

        # Re-read the config layers from disk into a fresh `SessionConfig` (only the
        # session-scoped parts), so a config change made after this process started is
        # picked up here rather than silently ignored. Then layer the CLI flags on top,
        # so a `--max-tool-calls-per-turn` passed to this invocation survives a `/clear`.
        reloaded_pc = load_process_config(
            config_flag_path=self._config_flag_path, cwd=workspace.path, workspace=workspace)
        self._process_config.session = reloaded_pc.session
        apply_cli_flags_to_session(self._process_config)
        new_session_config = self._process_config.session.model_copy()

        # Take note of any warnings / syntax errors raised when re-parsing config files.
        new_warnings: list[str] = reloaded_pc.config_warnings

        # The existing workspace is carried into the new session.
        new_session_config.apply_workspace_access(
            workspace=workspace, read_dirs=new_session_config.read_dirs,
            write_dirs=new_session_config.write_dirs)

        # The choice of model is a "live" setting that the user may have been manipulating
        # throughout; we carry forward their choice from the prior session, here.
        new_session_config.model = old_session.config.model
        new_session_config.thinking_effort = old_session.config.thinking_effort
        new_session_config.thinking_enabled = old_session.config.thinking_enabled

        # Once the new session_config is ready, wrap it up into the new Session.
        grants = compute_root_session_grants(
            self._process_config, new_session_config, new_session_config.role_name)
        new_session_config.skill_rules = grants.skill_rules
        self._session = Session(
            new_session_config,
            provider=old_session.provider,
            model_registry=old_session.model_registry,
            process_config=self._process_config,
            tool_registry=grants.tool_registry,
            effective_subagent_roles=grants.effective_subagent_roles,
        )
        self._selected_session = self._session
        self._selected_handle = None
        self._wire_session_notice_handler(self._session)
        self._wire_session_wake_handler(self._session)

        if self._session_log_enabled:
            log_path = session_log_path(self._session.id)
            configure_logging(repl_mode=True, log_path=log_path)
            logger.debug("Cleared session; now logging to %s", log_path)

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.remove_children()
        self._tool_call_widgets.clear()
        self._running_tool_call_widgets.clear()
        self._history_virtualizer = self._new_history_virtualizer(history)
        self._mount_mascot_greeting(history)
        history.mount(Static("Session cleared.", classes="notice"))

        # Display new config parser warnings after we've already cleared the history.
        for warning in new_warnings:
            self.show_notice(warning, error=True)

        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.clear_input_history()
        prompt_input.focus()
        self._update_status_bar()
        session_name = self.query_one(f"#{SESSION_NAME_ID}", Static)
        session_name.update(NEW_SESSION_LABEL)

        if initial_message is not None:
            # `call_after_refresh` queues the submit for after this refresh cycle, so the
            # freshly mounted history settles first, and is safe to call from either thread
            # (`post_message`-backed).
            self.call_after_refresh(self._submit_prompt, initial_message)

    def _submit_prompt(self, prompt_text: str) -> None:
        """Echo `prompt_text` into the history and dispatch it to the model."""
        if self._turn_in_flight:
            return
        self._turn_in_flight = True

        self._history_virtualizer.begin_trailing_region()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = Static(prompt_text, classes="prompt", markup=False)
        history.mount(prompt_widget)
        history.scroll_end(animate=False)

        self._cancel_event = threading.Event()
        self.refresh_bindings()
        self._send_prompt(prompt_text, self._cancel_event)

    @work(thread=True)
    def _send_prompt(self, prompt_text: str, cancel_event: threading.Event) -> None:
        """Send `prompt_text` to the model on a worker thread so the UI stays responsive."""
        self._turn_waiting_widget = self.call_from_thread(self._mount_turn_waiting_widget)

        def handle_session_name_changed(result: SessionName | None) -> None:
            self._handle_session_name_changed(result)

        def handle_skill_activated(skill_id: tuple[str, str]) -> None:
            namespace, name = skill_id
            self.call_from_thread(self.show_notice, f"Activated skill: {namespace}/{name}")

        response_widget: Markdown | None = None
        accumulated = ""
        response_round: int | None = None
        thinking_widget: Static | None = None
        thinking_accumulated = ""
        thinking_round: int | None = None
        reasoning_details_widget: Static | None = None
        reasoning_details_round: int | None = None
        round_index = 0

        def handle_chunk(delta_text: str) -> None:
            nonlocal accumulated, response_widget, response_round
            if response_round != round_index:
                response_widget = None
                accumulated = ""
                response_round = round_index
            accumulated += delta_text
            if response_widget is None:
                response_widget = self.call_from_thread(self._mount_response_widget, accumulated)
            else:
                self.call_from_thread(self._update_response_widget, response_widget, accumulated)

        def handle_thinking_chunk(delta_text: str) -> None:
            nonlocal thinking_accumulated, thinking_widget, thinking_round
            if thinking_round != round_index:
                thinking_widget = None
                thinking_accumulated = ""
                thinking_round = round_index
            thinking_accumulated += delta_text
            if thinking_widget is None:
                thinking_widget, _ = self.call_from_thread(
                    self._mount_thinking_widget, thinking_accumulated)
            else:
                self.call_from_thread(
                    self._update_thinking_widget, thinking_widget, thinking_accumulated)

        def handle_reasoning_details_chunk(entries: list[dict[str, Any]]) -> None:
            nonlocal reasoning_details_widget, reasoning_details_round
            if reasoning_details_round != round_index:
                reasoning_details_widget = None
                reasoning_details_round = round_index
            text = summarize_reasoning_details(entries)
            if text is None:
                return
            if reasoning_details_widget is None:
                reasoning_details_widget, _ = self.call_from_thread(
                    self._mount_reasoning_details_widget, text)
            else:
                self.call_from_thread(
                    self._update_reasoning_details_widget, reasoning_details_widget, text)

        def handle_tool_call_started(event: ToolCallStartedEvent) -> None:
            summary_text = self._render_tool_call_summary(event.name, event.args)
            self.call_from_thread(
                self._mount_running_tool_call_widget, event.call_id, summary_text)

        def handle_tool_call(event: ToolCallEvent) -> None:
            nonlocal round_index
            rendered = self._render_tool_call(event)
            running_widget = self._running_tool_call_widgets.pop(event.call_id, None)
            if running_widget is not None:
                self.call_from_thread(
                    self._finalize_running_tool_call_widget, running_widget, rendered)
            else:
                self.call_from_thread(self._mount_tool_call_widget, rendered)
            round_index += 1
            self.call_from_thread(self._maybe_refresh_task_sidebar_after_tool_call, event)
            # Nothing else re-arms the "still working" notice once a tool call clears it, so
            # without this the history goes silent for however long the model takes to start
            # its next round after this tool call's result.
            self._turn_waiting_widget = self.call_from_thread(self._mount_turn_waiting_widget)

        worker_thread_id = threading.get_ident()

        def call_on_app_thread(fn: Any, *args: Any) -> Any:
            """Run `fn(*args)` on the app's own thread and return its result."""
            if threading.get_ident() == worker_thread_id:
                return self.call_from_thread(fn, *args)
            return fn(*args)

        def handle_enqueue_message(queued_msg: QueuedMessage) -> None:
            """Create the italics "queued..." block in the history and save widget
            references in `queued_msg.history_data` so `handle_send_queued_message`
            can remove them later."""
            def _mount_queued_widgets() -> tuple[Static, Static]:
                history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
                header_widget = Static("<Queued message>", classes="queued-prompt-header")
                prompt_widget = Static(queued_msg.message_text, classes="queued-prompt", markup=False)
                history.mount(header_widget)
                history.mount(prompt_widget)
                history.scroll_end(animate=False)
                return header_widget, prompt_widget

            header_widget, prompt_widget = call_on_app_thread(_mount_queued_widgets)
            queued_msg.history_data = (header_widget, prompt_widget)

        def handle_send_queued_message(queued_msg: QueuedMessage) -> None:
            """Finalize `queued_msg`'s italics block now that it's been drained. A mid-turn
            drain (`deliver_queued_user_message`, fired while this turn's tool-call loop is
            still running) folds the message into the same turn without a fresh echoed
            widget, so de-italicize the block in place and leave it visible; an end-of-turn
            drain is about to be re-echoed as a new turn's prompt by `_submit_prompt`, so
            remove the block instead."""
            if queued_msg.history_data is None:
                return
            header_widget, prompt_widget = queued_msg.history_data

            def _finalize_widgets() -> None:
                header_widget.remove()
                if self._turn_in_flight:
                    prompt_widget.remove_class("queued-prompt")
                    prompt_widget.add_class("prompt")
                else:
                    prompt_widget.remove()

            call_on_app_thread(_finalize_widgets)

        callbacks = TurnEventHandlers(
            on_chunk=handle_chunk, on_thinking_chunk=handle_thinking_chunk,
            on_reasoning_details=handle_reasoning_details_chunk,
            cancel_event=cancel_event,
            on_tool_call_limit_reached=self._on_tool_call_limit_reached,
            on_permission_ask=self._on_permission_ask,
            on_ask_user_questions=self._on_ask_user_questions,
            on_escalate_privileges=self._on_escalate_privileges,
            on_tool_call_started=handle_tool_call_started,
            on_tool_call=handle_tool_call,
            on_session_name_changed=handle_session_name_changed,
            on_skill_activated=handle_skill_activated,
            on_enqueue_message=handle_enqueue_message,
            on_send_queued_message=handle_send_queued_message)
        # Stashed so `_finish_turn`'s end-of-turn `drain_queued_messages()` call has a live
        # `on_send_queued_message` hook to fire -- by the time that runs, `Session` has already
        # cleared its own `_current_turn_handlers`.
        self._active_turn_callbacks = callbacks

        # The outer try/finally guarantees `_turn_in_flight` is cleared however this worker
        # unwinds -- including a BaseException that slips past `except Exception` below and
        # would otherwise leave the flag stuck True forever.
        try:
            try:
                response_text = self._session.send_turn(prompt_text, callbacks)
            except ResponseAborted:
                self.call_from_thread(
                    self._handle_aborted_response, response_widget, accumulated,
                    thinking_widget, thinking_accumulated)
            except Exception as exc:
                self.call_from_thread(self._show_error, str(exc))
            else:
                if response_widget is not None:
                    self.call_from_thread(
                        self._finalize_streamed_response, response_widget, response_text)
                else:
                    self.call_from_thread(self._show_response, response_text)
        finally:
            # No-op on the normal path; only does anything when the worker unwound
            # without reaching a handler. Guarded because the event loop may already
            # be gone during teardown.
            try:
                self.call_from_thread(self._ensure_turn_finished, cancel_event)
            except Exception:
                pass

    def _handle_session_name_changed(self, result: SessionName | None) -> None:
        """React to `Session`'s `on_session_name_changed` callback firing: update the
        `SESSION_NAME_ID` status line to the derived title."""
        if self._selected_session is not self._session:
            return
        title = result.title if result is not None else (self._session.name or "")
        self.call_from_thread(self._update_session_name_line, title)

    def _finalize_streamed_response(self, widget: Markdown, response_text: str) -> None:
        """Reconcile a streamed `Markdown` widget with the final response and finish the turn."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(response_text)
        self._finish_turn(
            history, was_pinned, agent_turn_succeeded=True, response_text=response_text)

    def _show_response(self, response_text: str) -> None:
        """Append a model response to the history and re-enable the input box."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Markdown(response_text, classes="response"))
        self._finish_turn(
            history, was_pinned, agent_turn_succeeded=True, response_text=response_text)

    def _show_error(self, message: str) -> None:
        """Append an error message to the history and re-enable the input box."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Static(f"Error: {message}", classes="error", markup=False))
        self._finish_turn(history, was_pinned, agent_turn_succeeded=False)

    def _handle_aborted_response(
        self, response_widget: Markdown | None, response_text: str,
        thinking_widget: Static | None, thinking_text: str,
    ) -> None:
        """Leave the echoed prompt and every widget mounted for the aborted turn in place,
        tagging still-streaming widgets with an "(interrupted)" marker."""
        if response_widget is not None:
            response_widget.update(f"{response_text}\n\n*(interrupted)*")
        elif thinking_widget is not None:
            thinking_widget.update(f"{thinking_text}\n\n(interrupted)")
        else:
            history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
            history.mount(Static("(interrupted)", classes="interrupted"))

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._finish_turn(history, self._history_pinned_to_bottom, agent_turn_succeeded=False)

    def _scroll_if_pinned(self, history: VerticalScroll, was_pinned: bool) -> None:
        """Scroll `history` to its end iff `was_pinned`."""
        if was_pinned:
            history.scroll_end(animate=False)

    def _finish_turn(
        self, history: VerticalScroll, was_pinned: bool, *, agent_turn_succeeded: bool,
        response_text: str | None = None,
    ) -> None:
        """Scroll the history into view, refresh the token tally, re-enable the input, and
        either perform a session replacement deferred by `_start_replace_session` or drain any
        queued messages into a new turn."""
        self._clear_turn_waiting_widget()
        self._scroll_if_pinned(history, was_pinned)
        self._history_virtualizer.close_trailing_region()
        self._update_status_bar()
        self._session.persist_state()
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        self._update_prompt_input_disabled_state()
        if not input_widget.disabled:
            input_widget.focus()
        self._cancel_event = None
        self._shell_cancel_event = None
        self._turn_in_flight = False
        self._resolve_interrupt_notice()
        self._interrupt_notice_shown = False
        self.refresh_bindings()
        if self._pending_session_replacement:
            self._pending_session_replacement = False
            initial_message = self._pending_session_replacement_initial_message
            self._pending_session_replacement_initial_message = None
            self._do_replace_session(initial_message)
        else:
            # Drain any queued messages so the on_send_queued_message hook fires and, if any
            # were pending, fold them into a single new turn below.
            # `Session._current_turn_handlers` is already `None` by now, so the callbacks built
            # for that turn -- stashed by `_send_prompt` -- are passed explicitly instead.
            next_turn_text = self._session.drain_next_turn_text(self._active_turn_callbacks)
            self._active_turn_callbacks = None
            if next_turn_text is not None:
                self._submit_prompt(next_turn_text)
                self._quit_on_success = False
            elif not agent_turn_succeeded:
                self._quit_on_success = False
            elif self._quit_on_success:
                self._final_turn_response = response_text
                with self._watchdog.suspended():
                    self._session.close()
                self._begin_exit()
        if self._exit_requested:
            self.exit()

    def on_tui_session_wake(self, message: TuiSessionWake) -> None:
        """Handles a `TuiSessionWake`: drains and resubmits whatever an idle-triggered event or
        `reset_session` just queued."""
        if self._turn_in_flight:
            # A real user submission raced ahead of this wake; its own `_finish_turn` will
            # drain the same queued message once that turn ends.
            return
        next_turn_text = self._session.drain_next_turn_text()
        if next_turn_text is not None:
            self._submit_prompt(next_turn_text)
