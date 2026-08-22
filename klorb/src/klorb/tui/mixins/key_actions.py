# © Copyright 2026 Aaron Kimball
"""KeyActionsMixin: key handling, actions, quit/exit, and watchdog snoozing for ReplApp."""

import threading
import time
from typing import NoReturn

from textual import events
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from klorb.diagnostics import dump_all_thread_stacks, thread_dump_path
from klorb.process_config import user_config_path
from klorb.token_estimate import configure_tiktoken_cache_env
from klorb.tui._base import ReplAppBase
from klorb.tui.commands.init_commands import INIT_CONFIG_LABEL
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID, SUBAGENT_HISTORY_ID, TASK_SIDEBAR_ID
from klorb.tui.formatting import capture_scroll_anchor, random_greeting, restore_scroll_anchor
from klorb.tui.widgets.palette import PALETTE_PREFIX
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.task_sidebar import TaskSidebar
from klorb.tui.widgets.virtualized_history import VirtualizedHistoryContainer
from klorb.watchdog import force_exit

CONFIG_MISSING_MESSAGE = (
    f"Klorb configuration file not found. Run `{PALETTE_PREFIX}{INIT_CONFIG_LABEL}` to set up.")

MASCOT_ART = """\
      o
     /
    ▄▄▄
   █████
  ███████
 █░███x███
███████████
▟█▙     ▟█▙"""

_INTERRUPTING_MESSAGE = "Interrupting… (Ctrl+C again to quit)"
"""Shown in the history the first time Escape/Ctrl+C is pressed during an in-flight turn, so the
user gets immediate confirmation the keystroke was received."""

_INTERRUPTED_MESSAGE = "<Interrupted>"
"""What the `_INTERRUPTING_MESSAGE` notice is rewritten to once the interrupt has actually taken
hold."""

_DOUBLE_INTERRUPT_WINDOW_SECONDS = 1.0
"""How long a Ctrl+C press's effect on the next one lasts."""

_FORCE_EXIT_CLEANUP_GRACE_SECONDS = 3.0
"""How long the force-exit path waits for its best-effort cleanup to finish before calling
`os._exit` regardless."""

_CTRL_C_QUIT_WARNING = "Press Ctrl+C again to quit."
"""Shown in the history for an idle Ctrl+C press (nothing selected, nothing running)."""


class KeyActionsMixin(ReplAppBase):
    """Key handling, actions, quit/exit flow, and watchdog liveness snoozing."""

    def on_key(self, event: events.Key) -> None:
        """Redirect text-like keystrokes into the prompt input when they'd otherwise land
        on the (non-editable) history scroll."""
        if not event.is_printable or event.character is None:
            return
        focused = self.focused
        if focused is None or focused.id != HISTORY_ID:
            return
        try:
            prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        except Exception:
            return
        if prompt_input.disabled or not prompt_input.display or not prompt_input.can_focus:
            return
        # Move focus to the message box and let it own this keystroke: inserting here (rather
        # than re-posting the event) avoids a second dispatch through Textual's key pipeline
        # and keeps `PromptInput._on_key`'s history-detach/palette logic running exactly as if
        # the box had been focused all along.
        prompt_input.focus()
        prompt_input.insert(event.character)
        event.stop()
        event.prevent_default()

    def _something_abortable_for_selection(self) -> bool:
        """Whether there's anything for Escape/Ctrl+C to interrupt right now, for whichever
        session is currently selected: a subagent's own turn (`_selected_handle`) if one is
        selected, else the root session's turn/shell command (`_turn_in_flight`)."""
        if self._selected_handle is not None:
            return self._selected_handle.state == "running"
        return self._turn_in_flight

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide the `abort_response` binding from the footer unless something is currently
        running for the selected session, and `expand_history_placeholder` unless the visible
        history actually has a collapsed chunk to expand."""
        if action == "abort_response":
            return self._something_abortable_for_selection()
        if action == "expand_history_placeholder":
            return self._active_history_virtualizer().has_collapsed_chunks()
        return True

    def _active_history_virtualizer(self) -> VirtualizedHistoryContainer:
        """The `VirtualizedHistoryContainer` for whichever history is currently on screen:
        `#subagent-history`'s while a subagent is selected, else `#history`'s."""
        if self._selected_handle is not None and self._subagent_history_virtualizer is not None:
            return self._subagent_history_virtualizer
        return self._history_virtualizer

    async def action_expand_history_placeholder(self) -> None:
        """Ctrl+E: expand whichever collapsed chunk sits closest to the current scroll
        position in the visible history."""
        await self._active_history_virtualizer().expand_nearest_to_viewport()

    def action_abort_response(self) -> None:
        """Escape: interrupt whatever is currently running for the selected session."""
        self._interrupt_running_activity()

    def _interrupt_running_activity(self) -> None:
        """Show the `Interrupting…` notice and signal whatever's currently running to stop."""
        if self._selected_handle is not None:
            self._note_subagent_interrupt_requested(self._selected_handle)
            self._selected_handle.cancel_event.set()
            # A subagent parked on an interaction panel's decision never observes its cancel
            # event until that decision resolves; resolve it with the safe default now.
            self._release_pending_interactions(self._selected_handle.session.id)
            return
        self._note_interrupt_requested()
        if self._shell_cancel_event is not None:
            self._shell_cancel_event.set()
        else:
            self._signal_turn_cancellation()

    def _signal_turn_cancellation(self) -> None:
        """Tell an in-flight model turn's worker thread to unwind."""
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._release_pending_interactions(self._session.id)

    def _note_ctrl_c_copy(self) -> None:
        """Record that the most recent Ctrl+C (or Cmd+C) press copied selected text to the
        clipboard."""
        self._last_ctrl_c_at = time.monotonic()
        self._last_ctrl_c_kind = "copy"

    def _note_ctrl_c_quit_warning(self) -> None:
        """Mount the "press again to quit" notice into the history for an idle Ctrl+C press."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(_CTRL_C_QUIT_WARNING, classes="notice", markup=False))
        history.scroll_end(animate=False)

    def action_interrupt(self) -> None:
        """Ctrl+C: interrupt running activity, or warn/force-exit if idle."""
        now = time.monotonic()
        within_window = (now - self._last_ctrl_c_at) < _DOUBLE_INTERRUPT_WINDOW_SECONDS
        previous_kind = self._last_ctrl_c_kind
        already_acted_on_this_streak = within_window and previous_kind in ("interrupt", "bare")

        if self._something_abortable_for_selection() and not already_acted_on_this_streak:
            self._last_ctrl_c_at = now
            self._last_ctrl_c_kind = "interrupt"
            self._interrupt_running_activity()
            return

        if within_window and previous_kind == "bare":
            self._force_exit()  # NoReturn.
        self._last_ctrl_c_at = now
        self._last_ctrl_c_kind = "bare"
        self._note_ctrl_c_quit_warning()

    async def action_quit(self) -> None:
        """Ctrl+Q (and the built-in "Quit the application" system command): close the live
        session and exit."""
        with self._watchdog.suspended():
            self._session.close()
        self._begin_exit()

    def _begin_exit(self) -> None:
        """Exit the app, but never while a worker thread is still running."""
        if not self._turn_in_flight:
            self.exit()
            return
        self._exit_requested = True
        self._release_workers_for_exit()

    def _release_workers_for_exit(self) -> None:
        """Cancel an in-flight model turn (`_cancel_event`) and shell command
        (`_shell_cancel_event`), and resolve every pending interaction panel's decision with a
        safe default so a worker parked in `App.call_from_thread` awaiting one is released. Each
        piece is a no-op when that kind of work isn't running."""
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._shell_cancel_event is not None:
            self._shell_cancel_event.set()
        self._release_pending_interactions()

    def action_toggle_tool_call_detail(self) -> None:
        """Ctrl+O: flip every `ToolCallStatic` currently in the history between its one-line
        summary and its fuller detail view, all at once."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        anchor = capture_scroll_anchor(history)

        self._tool_call_detail_shown = not self._tool_call_detail_shown
        for widget in self._tool_call_widgets:
            widget.set_detail_shown(self._tool_call_detail_shown)
        label = "Hide" if self._tool_call_detail_shown else "Detail"
        self._bindings.key_to_bindings["ctrl+o"] = [
            Binding("ctrl+o", "toggle_tool_call_detail", label)]
        self.refresh_bindings()

        if anchor is not None:
            anchor_widget, line_offset = anchor
            self.call_after_refresh(restore_scroll_anchor, history, anchor_widget, line_offset)

    def on_mount(self) -> None:
        """Initialize the TUI: configure tiktoken cache, focus the input, set up scroll
        watchers, start the watchdog timer, and bootstrap workspace trust."""
        configure_tiktoken_cache_env()

        # Initialize sidebar visibility from config
        sidebar = self.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar)
        sidebar.display = self._active_sidebar == "tasks"

        self._start_subagents_panel_timer()

        self._watchdog.start()
        if self._watchdog.enabled:
            # Snooze from a main-thread timer, so the snooze stops exactly when the event loop wedges
            # (see docs/specs/interrupt-and-liveness-watchdog.md). Petting several times per
            # timeout keeps `_last_snooze` comfortably fresh on a healthy loop.
            self.set_interval(
                min(1.0, self._process_config.watchdog_timeout_seconds / 4), self._snooze_watchdog)

        input_widget = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        input_widget.border_title = "message"
        input_widget.styles.max_height = self._process_config.prompt_input_max_lines + 1
        input_widget.focus()
        self._update_status_bar()
        self._update_permission_badge()
        self._update_palette_hint()

        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._history_virtualizer = self._new_history_virtualizer(history)
        self.watch(history, "scroll_y", self._on_history_scroll_changed, init=False)

        subagent_history = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        self.watch(subagent_history, "scroll_y", self._on_subagent_history_scroll_changed, init=False)

        self._mount_mascot_greeting(history)

        if not user_config_path().is_file():
            history.mount(Static(CONFIG_MISSING_MESSAGE, classes="notice"))

        for warning in self._process_config.config_warnings:
            history.mount(Static(warning, classes="error", markup=False))

        self._run_startup_workspace_and_initial_message()

    def _mount_mascot_greeting(self, history: VerticalScroll) -> None:
        """Mount the klorb mascot art plus a freshly-picked random greeting into `history`."""
        history.mount(Static(f"{MASCOT_ART}\n\n{random_greeting()}", classes="mascot"))

    def on_unmount(self) -> None:
        """Disarm the liveness watchdog and stop the file-index observer as the app tears down."""
        self._watchdog.stop()
        if self._file_index is not None:
            self._file_index.close()

    def _snooze_watchdog(self) -> None:
        """Tell the liveness watchdog the event loop is alive."""
        self._watchdog.snooze()

    def _collect_hang_diagnostics(self) -> None:
        """Best-effort work done on the way out of a force-exit (from the double-Ctrl+C handler or
        the watchdog): dump every thread's stack for a later post-mortem, then save the session
        (when the workspace is trusted and there's anything to save) so a wedged conversation
        isn't lost. Runs on `force_exit`'s throwaway cleanup thread, time-boxed by
        `_FORCE_EXIT_CLEANUP_GRACE_SECONDS`, so blocking here can never prevent the exit; every
        step is independently guarded because the process is already doomed."""
        try:
            workspace = self._session.config.workspace
            dump_all_thread_stacks(thread_dump_path(workspace.path))
        except Exception:
            pass
        try:
            workspace = self._session.config.workspace
            if self._trust_manager is not None and workspace.trusted and self._session.messages:
                # `persist_state()`, not `close()`: the process is force-exiting via `os._exit`
                # right after this, so `session.lock` is deliberately left held -- the OS
                # reclaims it when this process's file descriptors close regardless, but
                # releasing it explicitly here would let a concurrently-running process mistake
                # a wedged-but-not-yet-reaped session for a cleanly closed one.
                self._session.persist_state()
        except Exception:
            pass

    def _force_exit(self) -> NoReturn:
        """Last-ditch escape from a wedged klorb: dump thread stacks + save the session
        (best-effort, time-boxed), then `os._exit`."""
        force_exit(self._collect_hang_diagnostics, _FORCE_EXIT_CLEANUP_GRACE_SECONDS)

    def _note_interrupt_requested(self) -> None:
        """Mount the `_INTERRUPTING_MESSAGE` notice into the history the first time Escape/Ctrl+C
        is pressed during the current turn, so the user gets immediate confirmation the keystroke
        landed (rather than wondering if the app has deadlocked). A no-op on repeat presses within
        the same turn; `_interrupt_notice_shown` is reset in `_finish_turn`."""
        if self._interrupt_notice_shown:
            return
        self._interrupt_notice_shown = True
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        notice = Static(_INTERRUPTING_MESSAGE, classes="notice interrupting", markup=False)
        self._interrupt_notice_widget = notice
        history.mount(notice)
        history.scroll_end(animate=False)

    def _resolve_interrupt_notice(self) -> None:
        """Rewrite this turn's `_INTERRUPTING_MESSAGE` notice to `_INTERRUPTED_MESSAGE` once the
        interrupt has actually taken hold."""
        if self._interrupt_notice_widget is not None:
            self._interrupt_notice_widget.update(_INTERRUPTED_MESSAGE)
            self._interrupt_notice_widget.remove_class("interrupting")
            self._interrupt_notice_widget.add_class("interrupted")
            self._interrupt_notice_widget = None

    def _ensure_turn_finished(self, own_cancel_event: threading.Event) -> None:
        """Backstop that runs `_finish_turn` iff `own_cancel_event` is still the active
        `_cancel_event`/`_shell_cancel_event` for an in-flight turn. A terminal handler may
        already have started a newer turn with its own cancel event by the time this runs, and
        the identity check leaves that newer turn alone."""
        if not self._turn_in_flight:
            return
        if self._cancel_event is not own_cancel_event and self._shell_cancel_event is not own_cancel_event:
            return
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._finish_turn(history, self._history_pinned_to_bottom, agent_turn_succeeded=False)

