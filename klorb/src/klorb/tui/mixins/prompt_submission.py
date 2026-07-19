# © Copyright 2026 Aaron Kimball
"""PromptSubmissionMixin: submitting a prompt or shell command and driving a turn to
completion for ReplApp."""

import logging
import threading
from typing import Any

from textual import work
from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static

from klorb.api_provider import ResponseAborted
from klorb.logging_config import configure_logging, session_log_path
from klorb.process_config import apply_cli_flags_to_session, load_process_config
from klorb.session import Session, ToolCallEvent, ToolCallStartedEvent, TurnEventHandlers
from klorb.session_naming import (
    _default_naming_model,
    _thinking_effort_for,
    generate_session_name,
    rename_session_id,
    session_id_suffix,
)
from klorb.tools.registry import ToolRegistry
from klorb.tui._base import ReplAppBase
from klorb.tui.constants import HISTORY_ID, NEW_SESSION_LABEL, PROMPT_INPUT_ID, SESSION_NAME_ID
from klorb.tui.formatting import _summarize_reasoning_details
from klorb.tui.shell import ShellCommandCancelled, ShellCommandTimedOut, UserShellCommand
from klorb.tui.widgets.prompt_input import PromptInput

logger = logging.getLogger(__name__)


class PromptSubmissionMixin(ReplAppBase):
    """Prompt submission, shell-command dispatch, and turn finalization -- kept as one
    mixin since `_send_prompt`'s `@work(thread=True)` body and `_finish_turn`/
    `_show_response`/etc. are one control flow, even though they are not contiguous in
    the original file. See `ReplApp` for how this mixes into the concrete app class."""

    def on_prompt_input_submitted(self, event: PromptInput.Submitted) -> None:
        """Echo the submitted prompt into the history and dispatch it to the model, or
        handle `:q`/`/quit`/`/exit` or a `!`-prefixed shell command synchronously.

        `PromptInput._execute_palette_hit` handles a palette selection (e.g. `>Clear
        session`) entirely on its own — invoking the hit's command directly rather than
        posting `Submitted` — so a `>`-prefixed `prompt_text` reaching here is always one
        that ruled out every palette option, dispatched as an ordinary prompt like any other
        (see `docs/specs/command-palette-from-prompt.md`).
        """
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

        self._submit_prompt(prompt_text)

    def _submit_shell_command(self, command: str) -> None:
        """Echo `!command` into the history, disable the input, and dispatch it to a worker
        thread (`_run_shell_command`) so a slow command can't block the UI. Only one shell
        command can be in flight at a time for this REPL: the input box stays disabled for the
        duration, exactly as it does for a model turn in `_submit_prompt`, so a user can't
        start a second one while the first is still running. The echoed `Static` is
        constructed with `markup=False`: `command` is arbitrary user input and must render
        verbatim rather than be parsed as Textual console markup.

        Like `_submit_prompt`, drops the submit if a turn or shell command is already running
        (`_turn_in_flight`) — the authoritative guard that keeps the two serial regardless of the
        racy `disabled` flag.
        """
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

    @work(thread=True)
    def _run_shell_command(self, command: str, cancel_event: threading.Event) -> None:
        """Run `command` via `UserShellCommand` on a worker thread, streaming its combined
        stdout/stderr into the history as it arrives (mirroring `_send_prompt`'s progressive
        rendering of a streamed model response). `output_lock` serializes the stdout and
        stderr pump threads `UserShellCommand.run()` spawns internally — both call back into
        `handle_output` concurrently, and without a lock two racing calls could each mount
        their own output widget, or one line could clobber another's addition to `accumulated`.

        The shell binary and timeout come from `ProcessConfig` (`shell_command`,
        `shell_timeout_seconds`); a timeout or a Ctrl+C interrupt (`action_interrupt`, which
        sets `cancel_event`) both kill the process and are reported as an error in the history
        rather than raised, matching how a failed/aborted model turn is displayed.
        """
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
        # including a BaseException past the handlers below -- see `_send_prompt` for the rationale.
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
                self.call_from_thread(self._ensure_turn_finished)
            except Exception:
                pass

    def _mount_shell_output_widget(self, initial_text: str) -> Static:
        """Mount a new `Static` widget for a streaming shell command's output and return it.

        Uses `Static` rather than the `Markdown` widget `_mount_response_widget` mounts for a
        streaming model reply: shell output is plain text, not markdown, and `Markdown`'s
        CommonMark rendering collapses a single newline within a paragraph into a soft line
        break (a space), which mangles multi-line command output. `Static` renders text
        verbatim, preserving every newline. Constructed with `markup=False` so a literal `[` in
        the output (e.g. an `[INFO]` log tag) can't be misread as the start of a Textual
        console markup tag and raise `MarkupError`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Static(initial_text, markup=False)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _update_shell_output_widget(self, widget: Static, text: str) -> None:
        """Update a streaming shell command's output `Static` widget with the latest
        accumulated `text`, following the view to the bottom only if it was already pinned
        there before this growth (see `_history_pinned_to_bottom`) — so a user who's scrolled
        up to reread earlier output isn't yanked back down by every incoming chunk.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)

    def _finish_shell_command(self, error_message: str | None) -> None:
        """Show `error_message` (if any) in the history, then finish the shell command's
        "turn" exactly like a model turn (`_finish_turn`): scroll to the end, refresh the
        token tally, and re-enable/refocus the input box.
        """
        if error_message is not None:
            self._show_error(error_message)
        else:
            history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
            self._finish_turn(history, self._history_pinned_to_bottom)

    def clear_session(self) -> None:
        """Replace the active Session with a fresh one (new id, config re-read from disk
        with CLI flags re-applied on top), reset the visible history, and roll over the
        per-session log file if session logging is enabled for this REPL invocation.

        Tears down the outgoing `Session` first (`Session.close()`) so a live resource it
        registered a teardown for (e.g. `BashTool`'s persistent shell) doesn't leak past this
        call — nothing else references the outgoing `Session` afterward.

        The new session's `SessionConfig` is rebuilt by re-reading the config layers from
        disk (keeping just the session-scoped parts) and applying the CLI flags on top, so a
        config-file edit made between startup and this `/clear` takes effect and a `--model`
        (etc.) flag survives a `/clear` the same way it survived startup — see
        `apply_cli_flags_to_session` and docs/specs/process-and-session-config.md.
        `_apply_workspace_config` is then called with the same workspace as before to fold in
        the workspace-trust-driven parts (project-layer `readDirs`/`writeDirs` grants,
        `config_warnings`) the same way it does when trust is first resolved.
        """
        old_session: Session = self._session

        workspace = old_session.config.workspace
        old_session.close()

        # Re-read the config layers from disk into a fresh `SessionConfig` (only the
        # session-scoped parts), so a config change made after this process started is
        # picked up here rather than silently ignored. Then layer
        # the CLI flags on top, so a `--max-tool-calls-per-turn` (etc.) passed to this
        # invocation survives a `/clear` the same way it survived startup.
        reloaded_pc = load_process_config(
            config_flag_path=self._config_flag_path, cwd=workspace.path, workspace=workspace)
        self._process_config.session = reloaded_pc.session
        apply_cli_flags_to_session(self._process_config)
        new_session_config = self._process_config.session.model_copy()

        # Take note of any warnings / syntax errors raised when re-parsing config files.
        new_warnings: list[str] = reloaded_pc.config_warnings

        # The existing workspace is brought into the new session the same way it
        # does when trust is first resolved; see docs/specs/process-and-session-config.md.
        new_session_config.workspace = workspace

        # The choice of model is a "live" setting that the user may have been manipulating
        # throughout; we carry forward their choice from the prior session, here.
        new_session_config.model = old_session.config.model
        new_session_config.thinking_effort = old_session.config.thinking_effort
        new_session_config.thinking_enabled = old_session.config.thinking_enabled

        # Once the new session_config is ready, wrap it up into the new Session.
        self._session = Session(
            new_session_config,
            provider=old_session.provider,
            model_registry=old_session.model_registry,
            process_config=self._process_config,
            tool_registry=ToolRegistry(self._process_config, new_session_config),
        )

        if self._session_log_enabled:
            log_path = session_log_path(self._session.id)
            configure_logging(repl_mode=True, log_path=log_path)
            logger.debug("Cleared session; now logging to %s", log_path)

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.remove_children()
        history.mount(Static("Session cleared.", classes="notice"))

        # Display new config parser warnings after we've already cleared the history.
        for warning in new_warnings:
            self.show_notice(warning, error=True)

        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.clear_input_history()
        prompt_input.focus()
        self._update_status_bar()
        self._session_naming_pending = True
        session_name = self.query_one(f"#{SESSION_NAME_ID}", Static)
        session_name.update(NEW_SESSION_LABEL)

    def _submit_prompt(self, prompt_text: str) -> None:
        """Echo `prompt_text` into the history, disable the input, and dispatch it. The echoed
        `Static` is constructed with `markup=False`: `prompt_text` is arbitrary user input and
        must render verbatim rather than be parsed as Textual console markup.

        Drops the submit outright if a turn or shell command is already running (`_turn_in_flight`):
        the input's `disabled` flag alone can't prevent a second submit that was queued before the
        first one disabled the box, so this authoritative check-and-set is what actually keeps turns
        serial (see `_turn_in_flight`).
        """
        if self._turn_in_flight:
            return
        self._turn_in_flight = True

        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = True

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = Static(prompt_text, classes="prompt", markup=False)
        history.mount(prompt_widget)
        history.scroll_end(animate=False)

        self._cancel_event = threading.Event()
        self.refresh_bindings()
        self._send_prompt(prompt_text, self._cancel_event)

    @work(thread=True)
    def _send_prompt(self, prompt_text: str, cancel_event: threading.Event) -> None:
        """Send `prompt_text` to the model on a worker thread so the UI stays responsive.

        Renders the response progressively as chunks stream in: a `Markdown` widget is
        mounted on the first chunk and updated on each subsequent one. Reasoning/thinking
        chunks, if the model sends any, are rendered the same way but behind a `<Thinking>`
        label and in italics, via a separate `Static` widget (not `Markdown`: reasoning text
        commonly spans multiple paragraphs, and Markdown's `*...*` emphasis syntax doesn't
        apply across blank-line-separated blocks, whereas Rich's `[italic]...[/italic]`
        console markup styles every line regardless). A structured `reasoning_details`
        payload, if the provider sends one, is rendered below the `<Thinking>` block as a
        one-line `<Reasoning>` indicator (see `_summarize_reasoning_details`) -- but only when
        it carries information the `<Thinking>` block can't show (e.g. an opaque/encrypted
        entry); a payload made entirely of plain-text entries is suppressed as redundant.

        A turn with tool calls is really a sequence of rounds under the hood (see
        `Session._dispatch_turn`): each round streams its own thinking/response text, then, if
        it ends in a tool-call request, `Session` dispatches those calls before starting the
        next round's stream. `on_tool_call` fires once per finished call, in between two
        rounds' streams, so seeing it is what marks a round boundary here — `round_index` is
        bumped on every such call, and `handle_chunk`/`handle_thinking_chunk` compare it
        against the round their current widget belongs to, starting a fresh widget rather than
        appending to the previous round's whenever it's moved on. Without this, one round's
        widget would keep absorbing every later round's text too, reading as a single
        ever-growing block that swallows the tool calls mounted in between rather than as
        separate, time-ordered blocks.

        If `cancel_event` is set (via Escape / `action_abort_response`) before the response
        finishes, `Session.send_turn()` raises `ResponseAborted`; the echoed prompt and any
        widgets already mounted for this turn's partial response/thinking/tool calls are left
        in place (`Session` keeps the same content in history, tagged `"aborted"` — see
        `_handle_aborted_response`) rather than torn down, and the input box is left empty for
        the next message rather than repopulated with this one. `response_widget`/
        `thinking_widget` at that point necessarily belong to the round that was still
        streaming when Escape fired, matching the single round's worth of content `Session`
        keeps for it.

        Before any of that, if `_session_naming_pending` is still `True` for the active
        `Session`, this is its first submitted prompt: `_run_session_naming` runs (and
        completes, or falls back) on this same worker thread before `Session.send_turn()` is
        called, so the first turn's own request never races the naming classifier's.
        """
        if self._session_naming_pending:
            self._run_session_naming(prompt_text)

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
            text = _summarize_reasoning_details(entries)
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
            summary_text, detail_text = self._render_tool_call(event)
            running_widget = self._running_tool_call_widgets.pop(event.call_id, None)
            if running_widget is not None:
                self.call_from_thread(
                    self._finalize_running_tool_call_widget,
                    running_widget, summary_text, detail_text)
            else:
                self.call_from_thread(self._mount_tool_call_widget, summary_text, detail_text)
            round_index += 1

        # The outer try/finally guarantees `_turn_in_flight` is cleared however this worker
        # unwinds -- including a BaseException (e.g. asyncio.CancelledError / worker cancellation)
        # that slips past `except Exception` below and would otherwise leave the flag stuck True
        # forever. See `_ensure_turn_finished` and docs/specs/interrupt-and-liveness-watchdog.md.
        try:
            try:
                response_text = self._session.send_turn(prompt_text, TurnEventHandlers(
                    on_chunk=handle_chunk, on_thinking_chunk=handle_thinking_chunk,
                    on_reasoning_details=handle_reasoning_details_chunk,
                    cancel_event=cancel_event,
                    on_tool_call_limit_reached=self._on_tool_call_limit_reached,
                    on_permission_ask=self._on_permission_ask,
                    on_ask_user_questions=self._on_ask_user_questions,
                    on_escalate_privileges=self._on_escalate_privileges,
                    on_tool_call_started=handle_tool_call_started,
                    on_tool_call=handle_tool_call))
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
            # No-op on the normal path (a handler above already finished the turn); only does
            # anything when the worker unwound without reaching one. Guarded because the event
            # loop may already be gone during teardown.
            try:
                self.call_from_thread(self._ensure_turn_finished)
            except Exception:
                pass

    def _run_session_naming(self, prompt_text: str) -> None:
        """Derive a `klorb.session_naming.SessionName` from `prompt_text` (this session's first
        submitted prompt) and apply it: rename `self._session.id` (keeping its timestamp
        prefix, swapping in the derived kebab-case slug), rename the session log file to
        match, and update the `SESSION_NAME_ID` status line to the derived title. Falls back to
        leaving `self._session.id` untouched and showing its own random nonce slug
        (`klorb.session_naming.session_id_suffix`) as the status line text if naming fails
        (timeout, unavailable classifier, malformed reply -- see `generate_session_name`'s own
        "never raises, returns `None` on failure" contract).

        Runs synchronously on `_send_prompt`'s worker thread, before `Session.send_turn()` is
        called -- see that method's docstring. Sets `_session_naming_pending = False`
        immediately (not only on success) so a failed/timed-out attempt is never retried on a
        later prompt within the same `Session`. Mounts a `GettingReadyStatic` into history for
        the call's duration (mirroring `_mount_running_tool_call_widget`'s "show progress while
        a worker-thread call is in flight" pattern), removing it in a `finally` so it's cleaned
        up on every exit path.
        """
        self._session_naming_pending = False
        getting_ready = self.call_from_thread(self._mount_getting_ready_widget)
        try:
            model = (
                self._process_config.session_classifier_model
                or _default_naming_model(self._session))
            reasoning = _thinking_effort_for(self._session, model)
            result = generate_session_name(
                prompt_text, api_provider=self._session.provider, model=model,
                timeout=self._process_config.session_classifier_timeout_seconds,
                e2e_timeout=self._process_config.session_classifier_e2e_timeout_seconds,
                reasoning=reasoning)
        finally:
            self.call_from_thread(getting_ready.remove_self)

        if result is None:
            self.call_from_thread(
                self._update_session_name_line, session_id_suffix(self._session.id))
            return

        new_id = rename_session_id(self._session.id, result.slug)
        if self._session_log_enabled:
            old_log_path = session_log_path(self._session.id)
            new_log_path = session_log_path(new_id)
            configure_logging(repl_mode=True, log_path=None)  # release the file handle
            if old_log_path.exists():
                old_log_path.rename(new_log_path)
            configure_logging(repl_mode=True, log_path=new_log_path)
        self._session.id = new_id
        self.call_from_thread(self._update_session_name_line, result.title)

    def _finalize_streamed_response(self, widget: Markdown, response_text: str) -> None:
        """Reconcile a streamed `Markdown` widget with the final response and finish the turn."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(response_text)
        self._finish_turn(history, was_pinned)

    def _show_response(self, response_text: str) -> None:
        """Append a model response to the history and re-enable the input box."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Markdown(response_text, classes="response"))
        self._finish_turn(history, was_pinned)

    def _show_error(self, message: str) -> None:
        """Append an error message to the history and re-enable the input box. Constructed
        with `markup=False`: `message` is often an exception's text (e.g. `str(OSError(...))`
        can read `[Errno 2] ...`), which must render verbatim rather than be parsed as Textual
        console markup.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        history.mount(Static(f"Error: {message}", classes="error", markup=False))
        self._finish_turn(history, was_pinned)

    def _handle_aborted_response(
        self, response_widget: Markdown | None, response_text: str,
        thinking_widget: Static | None, thinking_text: str,
    ) -> None:
        """Leave the echoed prompt and every widget mounted for the aborted turn in place
        (`Session` keeps the same content in history, tagged `processing_state="aborted"`,
        rather than discarding it), tagging whichever of the response/thinking widgets was
        still streaming when Escape fired with an "(interrupted)" marker. If neither had
        started streaming yet (the turn was interrupted before its first chunk, e.g. during an
        earlier round's tool-call dispatch), mount a standalone marker instead so the
        interruption is still visible. The input box is left empty rather than repopulated
        with the original prompt, since that prompt is now a real, if incomplete, turn in
        history rather than a draft to resend.
        """
        if response_widget is not None:
            response_widget.update(f"{response_text}\n\n*(interrupted)*")
        elif thinking_widget is not None:
            thinking_widget.update(f"{thinking_text}\n\n(interrupted)")
        else:
            history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
            history.mount(Static("(interrupted)", classes="interrupted"))

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._finish_turn(history, self._history_pinned_to_bottom)

    def _scroll_if_pinned(self, history: VerticalScroll, was_pinned: bool) -> None:
        """Scroll `history` to its end iff `was_pinned` — `_history_pinned_to_bottom`'s value
        captured immediately before the content that just changed was applied — so streaming
        updates follow the user to the bottom only when they hadn't already scrolled away from
        it.
        """
        if was_pinned:
            history.scroll_end(animate=False)

    def _finish_turn(self, history: VerticalScroll, was_pinned: bool) -> None:
        """Scroll the history into view (iff `was_pinned`, see `_scroll_if_pinned`), refresh the
        token tally, and hand focus back to the input box. Also clears the in-flight turn's
        cancel event and pending-prompt tracking (model turn) and the shell command's cancel
        event (`_run_shell_command`), since neither Escape nor Ctrl+C has anything left to
        abort/interrupt once a turn is done (successfully, in error, or aborted) — shared by
        both a model turn and a shell command, since only one of the two is ever in flight at a
        time. Clearing `_turn_in_flight` here (the terminal state of every model turn and shell
        command) is what lets the next submit through, and resetting `_interrupt_notice_shown`
        re-arms the one-time `Interrupting…` notice for the next turn. Just before that reset,
        `_resolve_interrupt_notice` rewrites this turn's `Interrupting…` notice (if any) to
        `<Interrupted>`, now that reaching here means the interrupt has actually taken hold.

        Reached on every path where `Session.send_turn`/the shell command returns or raises, via a
        terminal handler; `_send_prompt`/`_run_shell_command` additionally call it from a `finally`
        (through `_ensure_turn_finished`) so a `BaseException` unwind of the worker still gets here.
        Idempotent enough for that backstop: a second call after the flag is already cleared just
        re-does harmless UI resets.

        If a quit was requested while this turn was still running (`_exit_requested`, set by
        `_begin_exit`), the deferred `self.exit()` happens here — now that the worker thread has
        actually finished and can no longer be stranded by the event loop stopping out from under
        it. Runs last, after the turn is fully wound down.
        """
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.disabled = False
        input_widget.focus()
        self._cancel_event = None
        self._shell_cancel_event = None
        self._turn_in_flight = False
        self._resolve_interrupt_notice()
        self._interrupt_notice_shown = False
        self.refresh_bindings()
        if self._exit_requested:
            self.exit()
