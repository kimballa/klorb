# © Copyright 2026 Aaron Kimball
"""KeyActionsMixin: key handling, actions, quit/exit, and watchdog snoozing for ReplApp."""

import time
from typing import NoReturn

from textual import events, work
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from klorb.diagnostics import dump_all_thread_stacks, thread_dump_path
from klorb.process_config import user_config_path
from klorb.token_estimate import configure_tiktoken_cache_env
from klorb.tui._base import ReplAppBase
from klorb.tui.commands.init_commands import INIT_CONFIG_LABEL
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID, TASK_SIDEBAR_ID
from klorb.tui.formatting import random_greeting
from klorb.tui.panels.confirm_screen import SaveOnQuitScreen
from klorb.tui.widgets.palette import PALETTE_PREFIX
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.task_sidebar import TaskSidebar
from klorb.watchdog import force_exit
from klorb.workspace.last_session import clear_last_session, write_last_session

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

MASCOT_GREETING = random_greeting()

_INTERRUPTING_MESSAGE = "Interrupting… (Ctrl+C again to quit)"
"""Shown in the history the first time Escape/Ctrl+C is pressed during an in-flight turn, so the
user gets immediate confirmation the keystroke was received (rather than wondering if the app has
deadlocked) — see `KeyActionsMixin._note_interrupt_requested`."""

_INTERRUPTED_MESSAGE = "<Interrupted>"
"""What the `_INTERRUPTING_MESSAGE` notice is rewritten to once the interrupt has actually taken
hold — i.e. the turn it was interrupting has finished winding down (`_finish_turn` →
`_resolve_interrupt_notice`), turning the transient "Interrupting…" into a settled record that it
did."""

_DOUBLE_INTERRUPT_WINDOW_SECONDS = 1.0
"""How long a Ctrl+C press's effect on the next one lasts, for `KeyActionsMixin.action_interrupt`'s
streak escalation (interrupt/copy, then a warning, then a force-exit via
`KeyActionsMixin._force_exit` — two presses total from a bare/idle start, three from a copy or an
interrupt) — see docs/specs/interrupt-and-liveness-watchdog.md's "Ctrl+C semantics" section."""

_FORCE_EXIT_CLEANUP_GRACE_SECONDS = 3.0
"""How long the force-exit path waits for its best-effort cleanup (stack dump + session save) to
finish before calling `os._exit` regardless — see `KeyActionsMixin._force_exit` and
`klorb.watchdog.force_exit`."""

_CTRL_C_QUIT_WARNING = "Press Ctrl+C again to quit."
"""Shown in the history for an idle Ctrl+C press (nothing selected, nothing running) — a second
press within `_DOUBLE_INTERRUPT_WINDOW_SECONDS` force-exits — see
`KeyActionsMixin._note_ctrl_c_quit_warning`/`action_interrupt`."""


class KeyActionsMixin(ReplAppBase):
    """Key handling, actions, quit/exit flow, and watchdog liveness snoozing -- see
    `ReplApp` for how this mixes into the concrete app class."""

    def on_key(self, event: events.Key) -> None:
        """Redirect text-like keystrokes into the prompt input when they'd otherwise land
        on the (non-editable) history scroll.

        The history `VerticalScroll` can receive focus — by clicking it or tabbing into it
        — but it isn't an editable widget, so typing there would silently do nothing. Rather
        than make the user click back into the box to start typing, treat a printable
        keystroke that reaches this App-level handler while the history is focused as a
        request to type into the message box: move focus there and insert the character,
        so the keystroke isn't lost and the box is ready for the rest of what the user is
        typing. Non-printable keys (arrows, escape, ctrl-combos, ...) are left alone, since
        those are navigation/controls the history scroll legitimately handles or the App's
        own bindings consume.

        Only acts when the history scroll itself is the focused widget, and only when the
        prompt input is in a state to accept input — i.e. not disabled while an interaction
        panel (a permission ask or an `AskUserQuestions` prompt) or a modal is active.
        Textual keeps an active modal on its own screen, so the base `Screen`/`App` key
        dispatch routes that screen's events there before this handler ever runs; this guard
        keeps the redirect from fighting a focused modal's own input widgets if one should
        ever share the history's widget identity (it doesn't today, but the check is cheap).
        """
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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Hide the `abort_response` binding from the footer unless something is currently
        running (a streaming model turn — including a synchronous Bash tool call inside it — or
        a `!`-prefixed shell command), since there's nothing for Escape to abort otherwise.
        """
        if action == "abort_response":
            return self._turn_in_flight
        return True

    def action_abort_response(self) -> None:
        """Escape: interrupt whatever is currently running (an in-flight model turn — including
        a Bash tool call inside it, see `Session.active_cancel_event` — or a `!`-prefixed shell
        command), the same as a solitary Ctrl+C (`action_interrupt`) — but Escape only ever does
        this; it has no copy-text or quit-warning meaning."""
        self._interrupt_running_activity()

    def _interrupt_running_activity(self) -> None:
        """Show the `Interrupting…` notice and signal whatever's currently running to stop:
        a `!`-prefixed shell command's `_shell_cancel_event`, or an in-flight model turn's
        `_cancel_event` (`_signal_turn_cancellation`) — which also reaches a synchronous Bash
        tool call running inside that turn via `Session.active_cancel_event`, causing it to send
        `SIGINT` to its own child process rather than waiting for the next tool-call boundary.
        Shared by Escape (`action_abort_response`) and a Ctrl+C that lands on running activity
        (`action_interrupt`)."""
        self._note_interrupt_requested()
        if self._shell_cancel_event is not None:
            self._shell_cancel_event.set()
        else:
            self._signal_turn_cancellation()

    def _signal_turn_cancellation(self) -> None:
        """Tell an in-flight model turn's worker thread to unwind. Sets `_cancel_event` (so
        `Session.send_turn` raises `ResponseAborted` at its next stream or round/tool boundary —
        see `Session._dispatch_turn`) *and* resolves any pending interaction panel's decision with
        a safe deny/cancelled default (`_release_pending_interaction`), so a worker parked in
        `App.call_from_thread` awaiting a user decision is released too — the cancel event alone
        can't wake it, since it's blocked on the decision future, not mid-stream. A no-op when
        nothing is pending, so it's safe to call unconditionally."""
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._release_pending_interaction is not None:
            self._release_pending_interaction()

    def _note_ctrl_c_copy(self) -> None:
        """Record that the most recent Ctrl+C (or Cmd+C) press copied selected text to the
        clipboard, for `action_interrupt`'s own streak bookkeeping. Called by
        `SelectionSafeScreen.action_copy_text`/`PromptInput.action_copy` rather than from
        `action_interrupt` itself: Textual's own binding-chain resolution runs those bindings
        *ahead of* this app's `action_interrupt` and never falls through to it at all once a
        selection exists to copy — see docs/specs/interrupt-and-liveness-watchdog.md's "Ctrl+C
        semantics" section."""
        self._last_ctrl_c_at = time.monotonic()
        self._last_ctrl_c_kind = "copy"

    def _note_ctrl_c_quit_warning(self) -> None:
        """Mount the "press again to quit" notice into the history for an idle Ctrl+C press —
        see `action_interrupt`'s `"bare"` case."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.mount(Static(_CTRL_C_QUIT_WARNING, classes="notice", markup=False))
        history.scroll_end(animate=False)

    def action_interrupt(self) -> None:
        """Ctrl+C: behavior depends on what's true right now, mirroring a terminal's own Ctrl+C
        interrupting the foreground job rather than closing the shell (see docs/specs/interrupt-
        and-liveness-watchdog.md's "Ctrl+C semantics" section):

        * **Selected text.** Never reaches this method at all: Textual's own `Screen`/`TextArea`
          bindings for Ctrl+C (`SelectionSafeScreen.action_copy_text`/`PromptInput.action_copy`)
          run earlier in the binding chain and copy-and-stop, only falling through here (via
          `SkipAction`) once nothing is selected. Both record the press via `_note_ctrl_c_copy`
          so the streak logic below still knows a Ctrl+C just happened.
        * **Something running** (`_turn_in_flight`: an in-flight model turn — including a
          synchronous Bash tool call inside it — or a `!`-prefixed shell command) — *and* the
          immediately preceding press, if any within the window, wasn't itself an interrupt or a
          bare "nothing to do" press: interrupt it, identically to Escape
          (`_interrupt_running_activity`). This only ever fires once per streak; it does not
          re-signal on a later press even if the activity is still in flight, so it can't loop.
        * **Everything else** (nothing running, or something's still running but this streak
          already interrupted it once): shows a "press again to quit" notice
          (`_note_ctrl_c_quit_warning`) — unless the *immediately preceding* press was itself one
          of these "nothing new to do" presses within the window, in which case this one
          force-exits (`_force_exit`, i.e. `os._exit`) instead.

        Net effect: a solitary bare Ctrl+C (nothing selected, nothing running) takes two presses
        to force-exit (warn, then quit); a copy or an interrupt takes three (the copy/interrupt
        itself, then a warning, then a quit) — see docs/adrs/idle-ctrl-c-force-exits-not-the-
        polite-quit-flow.md. A running activity is deliberately interrupted rather than the app
        being quit out from under it: that would strand the turn's worker thread and hang
        process shutdown (see `_begin_exit`), so interrupting-then-quitting is both safer and the
        more conventional terminal gesture. This handler runs on the event loop, so its
        force-exit escalation only helps when the loop is still alive — a wedged loop is the
        `LivenessWatchdog`'s job instead.
        """
        now = time.monotonic()
        within_window = (now - self._last_ctrl_c_at) < _DOUBLE_INTERRUPT_WINDOW_SECONDS
        previous_kind = self._last_ctrl_c_kind
        already_acted_on_this_streak = within_window and previous_kind in ("interrupt", "bare")

        if self._turn_in_flight and not already_acted_on_this_streak:
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
        """Ctrl+Q (and the built-in "Quit the application" system command): ask whether to
        save the session state, then exit — see `_quit_after_maybe_saving`.

        Overrides `App.action_quit` (which just calls `self.exit()`) keeping its exact
        signature (`async def action_quit(self) -> None`) so it stays a valid override;
        the actual work happens in `_quit_after_maybe_saving`, a `@work()` worker, because
        awaiting a modal's dismissal (`push_screen_wait`) is only allowed from within an
        active worker's context. Calling (not awaiting) a `@work()`-decorated method starts
        the worker and returns immediately, same as `trust_workspace`.
        """
        self._quit_after_maybe_saving()

    @work()
    async def _quit_after_maybe_saving(self) -> None:
        """If the current workspace is trusted and this app has a `TrustManager` (see
        `workspace_trust_management_enabled`), ask whether to save the session state before
        quitting via `SaveOnQuitScreen`; "Yes" writes the live `Session`'s config and message
        history to `last-session.json` (`klorb.workspace.last_session.write_last_session`) so
        `_maybe_restore_last_session` can pick it back up the next time klorb opens this
        workspace, "No" quits without saving, and "Cancel" (or Escape) aborts the quit entirely —
        this method returns without calling `_begin_exit()`, leaving the session running exactly
        as before. A no-op prompt (skipping straight to `self.exit()`, with nothing to cancel out
        of) when there's no `TrustManager` or the workspace isn't trusted, since an unresolved or
        untrusted workspace has no business writing into its per-project data directory, or
        there's no message history to save.
        """
        has_messages = len(self._session.messages) > 0
        save = False
        if self._trust_manager is not None and self._session.config.workspace.trusted and has_messages:
            choice = await self.push_screen_wait(
                SaveOnQuitScreen("Save session state before quitting?"))
            if choice == "cancel":
                return
            save = choice == "save"

        if save:
            write_last_session(
                self._session.config.workspace, self._session.config, self._session.messages,
                statistics=self._session.statistics,
                session_id=self._session.id,
                root_id=self._session.root_id,
                session_name=self._session.name,
                cur_chainlink_task_id=self._session.cur_chainlink_task_id)
        else:
            clear_last_session(self._session.config.workspace)
            self._session.close()
        self._begin_exit()

    def _begin_exit(self) -> None:
        """Exit the app, but never while a worker thread is still running: if a model turn or shell
        command is in flight (`_turn_in_flight`), exiting immediately would stop the event loop
        while that worker is parked in `App.call_from_thread`, so its `future.result()` would never
        return and its non-daemon executor thread would block process shutdown until it's ^C'd (the
        "TUI is gone but the process won't end" hang). Instead, signal the worker to unwind
        (`_release_workers_for_exit`) and defer the real `self.exit()` to `_finish_turn`, which the
        worker reaches once its turn actually ends. When nothing is in flight, exits right away."""
        if not self._turn_in_flight:
            self.exit()
            return
        self._exit_requested = True
        self._release_workers_for_exit()

    def _release_workers_for_exit(self) -> None:
        """Signal every kind of in-flight worker to stop so a deferred exit (`_begin_exit`) can
        complete: cancel a streaming/tool model turn (`_cancel_event`) and a shell command
        (`_shell_cancel_event`), and resolve any pending interaction panel's decision with a safe
        default (`_release_pending_interaction`) so a worker parked in `App.call_from_thread`
        awaiting one is released. Each piece is a no-op when that kind of work isn't running."""
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._shell_cancel_event is not None:
            self._shell_cancel_event.set()
        if self._release_pending_interaction is not None:
            self._release_pending_interaction()

    def action_toggle_tool_call_detail(self) -> None:
        """Ctrl+O: flip every `ToolCallStatic` currently in the history — from any turn, not
        just the latest — between its one-line summary and its fuller detail view, all at once.

        Also updates the footer's own label for this binding — `"Detail"` while summaries are
        shown (offering to reveal detail), `"Hide"` once detail is shown (offering to go back)
        — since a binding's description is otherwise fixed at `BINDINGS` class-definition time.
        `self._bindings` is this `ReplApp` instance's own mutable copy of the merged class-level
        `BINDINGS` (see `DOMNode.__init__`), so replacing its `"ctrl+o"` entry only affects this
        instance; `refresh_bindings()` is Textual's documented hook for prompting `Footer` to
        redraw after such a change.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        anchor = self._capture_scroll_anchor(history)

        self._tool_call_detail_shown = not self._tool_call_detail_shown
        for widget in self._tool_call_widgets:
            widget.set_detail_shown(self._tool_call_detail_shown)
        label = "Hide" if self._tool_call_detail_shown else "Detail"
        self._bindings.key_to_bindings["ctrl+o"] = [
            Binding("ctrl+o", "toggle_tool_call_detail", label)]
        self.refresh_bindings()

        if anchor is not None:
            anchor_widget, line_offset = anchor
            self.call_after_refresh(self._restore_scroll_anchor, history, anchor_widget, line_offset)

    @staticmethod
    def _capture_scroll_anchor(history: VerticalScroll) -> tuple[Widget, int] | None:
        """Identify whichever direct child of `history` currently occupies the top of the
        viewport, and how many of its own lines are already scrolled past, so a layout change
        that resizes other children (e.g. `Ctrl+O` toggling tool-call detail — see
        `action_toggle_tool_call_detail`) can restore that same child to that same on-screen
        line afterward instead of leaving `scroll_y` pointing at whatever unrelated content
        now happens to occupy that offset. Returns `None` only when `history` has no children
        to anchor to.
        """
        children = list(history.children)
        if not children:
            return None
        scroll_y = int(history.scroll_y)
        for child in children:
            region = child.virtual_region
            if region.y + region.height > scroll_y:
                return child, max(0, scroll_y - region.y)
        last = children[-1]
        return last, max(0, last.virtual_region.height - 1)

    @staticmethod
    def _restore_scroll_anchor(history: VerticalScroll, anchor_widget: Widget, line_offset: int) -> None:
        """Scroll `history` so `anchor_widget` sits at the same on-screen line it held when
        `_capture_scroll_anchor` recorded `line_offset`. Runs via `call_after_refresh` (see
        caller), which is what makes `anchor_widget.virtual_region` reflect the widget's
        post-change height rather than a stale pre-refresh layout; `scroll_to` itself clamps
        to the valid scroll range, so an offset past the (possibly now-shorter) content simply
        lands at the bottom instead of overshooting.
        """
        region = anchor_widget.virtual_region
        history.scroll_to(y=region.y + line_offset, animate=False, immediate=True)

    def on_mount(self) -> None:
        """Point tiktoken at the `klorb init`-installed cache (see
        `configure_tiktoken_cache_env()`) now that the app is actually running, so its log
        message routes through the app's log / the session log file rather than leaking to
        raw stderr ahead of the TUI taking over the terminal — see
        docs/adrs/configure-tiktoken-cache-env-after-repl-app-mounts.md. Then label and focus
        the input box, cap its growth at the configured max height, watch the history's scroll
        position (see `_on_history_scroll_changed`), show the initial `> palette` hint (the box
        starts empty), greet the user with the klorb mascot (see
        `MASCOT_ART`/`MASCOT_GREETING`), note in the history if no per-user config file exists
        yet (see `CONFIG_MISSING_MESSAGE`), reports any config layer that failed to parse (see
        `ProcessConfig.config_warnings`), then hand off to
        `_run_startup_workspace_and_initial_message` to resolve (and, if this is a brand-new
        workspace, interactively bootstrap) workspace trust before submitting any initial
        message as the first turn.
        """
        configure_tiktoken_cache_env()

        # Initialize task sidebar visibility from config
        sidebar = self.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar)
        sidebar.display = self._task_sidebar_shown

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
        self.watch(history, "scroll_y", self._on_history_scroll_changed, init=False)

        history.mount(Static(f"{MASCOT_ART}\n\n{MASCOT_GREETING}", classes="mascot"))

        if not user_config_path().is_file():
            history.mount(Static(CONFIG_MISSING_MESSAGE, classes="notice"))

        for warning in self._process_config.config_warnings:
            history.mount(Static(warning, classes="error", markup=False))

        self._run_startup_workspace_and_initial_message()

    def on_unmount(self) -> None:
        """Disarm the liveness watchdog as the app tears down, so it can't fire during the
        post-run shutdown window (crash handling, session save) once the event loop has stopped
        snoozing it — see `_snooze_watchdog` and docs/specs/interrupt-and-liveness-watchdog.md."""
        self._watchdog.stop()

    def _snooze_watchdog(self) -> None:
        """Tell the liveness watchdog the event loop is alive. Driven by a `set_interval` timer on
        the main thread (see `on_mount`), so it stops exactly when the loop wedges."""
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
                write_last_session(
                    workspace, self._session.config, self._session.messages,
                    statistics=self._session.statistics,
                    session_id=self._session.id,
                    root_id=self._session.root_id,
                    session_name=self._session.name,
                    cur_chainlink_task_id=self._session.cur_chainlink_task_id)
        except Exception:
            pass

    def _force_exit(self) -> NoReturn:
        """Last-ditch escape from a wedged klorb: dump thread stacks + save the session
        (best-effort, time-boxed), then `os._exit`. Called by the watchdog (from its own thread)
        and by the double-Ctrl+C handler (`action_interrupt`). Never returns — see
        `klorb.watchdog.force_exit` and docs/specs/interrupt-and-liveness-watchdog.md."""
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
        interrupt has actually taken hold — called from `_finish_turn`, which is reached only
        after the interrupted turn has finished winding down. A no-op when no interrupt notice
        was shown this turn (`_interrupt_notice_widget is None`), so an uninterrupted turn's
        completion leaves the history untouched. Clears `_interrupt_notice_widget` afterward so
        the next turn starts fresh (`_interrupt_notice_shown` is reset alongside it in
        `_finish_turn`)."""
        if self._interrupt_notice_widget is not None:
            self._interrupt_notice_widget.update(_INTERRUPTED_MESSAGE)
            self._interrupt_notice_widget.remove_class("interrupting")
            self._interrupt_notice_widget.add_class("interrupted")
            self._interrupt_notice_widget = None

    def _ensure_turn_finished(self) -> None:
        """Backstop that runs `_finish_turn` iff a turn is still marked in flight — i.e. no
        terminal handler already finished it. Called from `_send_prompt`/`_run_shell_command`'s
        `finally` so that even a `BaseException` unwind of the worker thread (e.g.
        `asyncio.CancelledError`, which slips past `except Exception`) still clears
        `_turn_in_flight` and re-enables input. A no-op on the normal path, where a handler
        already cleared the flag. See docs/specs/interrupt-and-liveness-watchdog.md."""
        if not self._turn_in_flight:
            return
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        self._finish_turn(history, self._history_pinned_to_bottom)

