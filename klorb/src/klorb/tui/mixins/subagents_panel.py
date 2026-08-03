# © Copyright 2026 Aaron Kimball
"""SubagentsPanelMixin: the Ctrl+G-toggled subagents panel, (sub)agent selection, the selected
subagent's transcript view, and the selection-gated ask-attention bookkeeping `InteractionsMixin`
polls -- see `klorb.tui.widgets.subagents_panel.SubagentsPanel` and docs/specs/subagents.md's
"Subagents panel" section."""

import asyncio

from textual.containers import VerticalScroll
from textual.widgets import Markdown, OptionList, Static

from klorb.agents.runtime import SUBAGENT_ABORTED_MARKER, SessionTreeNode, SubagentHandle, walk_session_tree
from klorb.message import Message as ChatMessage
from klorb.session import Session
from klorb.tui._base import ReplAppBase
from klorb.tui.constants import (
    HISTORY_ID,
    NEW_SESSION_LABEL,
    PROMPT_INPUT_ID,
    SESSION_NAME_ID,
    SUBAGENT_ATTENTION_STATUS_ID,
    SUBAGENT_HISTORY_ID,
    SUBAGENTS_PANEL_ID,
    TASK_SIDEBAR_ID,
)
from klorb.tui.formatting import (
    pinned_to_bottom,
    resolve_thinking_body_text,
    strip_system_interjections,
    summarize_reasoning_details,
)
from klorb.tui.mixins.rendering import REASONING_DETAILS_LABEL, THINKING_LABEL, TOOL_USE_LABEL
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.subagents_panel import (
    SUBAGENTS_LIST_ID,
    SubagentPanelOption,
    SubagentRowData,
    SubagentsPanel,
)
from klorb.tui.widgets.task_sidebar import TaskSidebar
from klorb.tui.widgets.tool_call_widgets import ToolCallStatic

_PANEL_TICK_INTERVAL_SECONDS = 0.6
"""How often `_tick_subagents_panel` fires: blinks the `(!)` attention marker and refreshes the
selected subagent's transcript (new messages, and the trailing status notice's text) so the view
doesn't look frozen mid-turn."""

_ASK_GATE_POLL_INTERVAL_SECONDS = 0.2
"""How often `_await_session_selected` re-checks whether its target session has become selected
-- mirrors `klorb.agents.runtime.SubagentTracker`'s own poll-with-short-timeout pattern rather
than a new synchronization primitive."""

_SUBAGENT_STILL_RUNNING_NOTICE = "Subagent is still working…"
_SUBAGENT_TASK_COMPLETE_NOTICE = "Subagent task complete."
_SUBAGENT_SENDING_INTERRUPT_NOTICE = "Sending interrupt…"
_SUBAGENT_INTERRUPTED_NOTICE = "Subagent interrupted."


class SubagentsPanelMixin(ReplAppBase):
    """Ctrl+G shows or hides a docked right-hand panel listing every session in the live
    subagent tree, with click/arrow-key row selection that switches the displayed transcript --
    see `ReplApp` for how this mixes into the concrete app class."""

    def action_toggle_subagents_panel(self) -> None:
        """Ctrl+G: show or hide the subagents panel. Mutually exclusive with the task sidebar --
        showing this one hides that one (see `TaskSidebarMixin.action_toggle_task_sidebar` for
        the reverse direction) -- since both dock the same right-hand slot and the plan calls for
        "either tasks or subagents ... visible at once."."""
        panel = self.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel)
        self._subagents_panel_shown = not self._subagents_panel_shown
        panel.display = self._subagents_panel_shown
        if self._subagents_panel_shown and self._task_sidebar_shown:
            self._task_sidebar_shown = False
            self.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar).display = False
        if self._subagents_panel_shown:
            self._refresh_subagents_panel()
            self.query_one(f"#{SUBAGENTS_LIST_ID}", OptionList).focus()
        else:
            self._update_subagent_attention_status_line()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Switch the displayed transcript as soon as a row is highlighted -- by an up/down
        arrow press or a click -- rather than requiring a separate `Enter` to "select" it, since
        the plan describes moving focus between agents as the selection gesture itself.

        A no-op when the highlighted row already names the selected session: `show_rows` resets
        the `OptionList` (`clear_options()` then `add_options()`) on every refresh, which posts a
        fresh `OptionHighlighted` for whatever index it re-highlights even when that index's
        session hasn't changed -- without this guard, `_tick_subagents_panel`'s periodic refresh
        would re-trigger `_select_session` (and its `_refresh_subagents_panel()` call, which
        triggers another `OptionHighlighted` next tick) forever.
        """
        if event.option_list.id != SUBAGENTS_LIST_ID:
            return
        option = event.option
        assert isinstance(option, SubagentPanelOption)
        if option.session_id == self._selected_session.id:
            return
        self._select_session(option.session_id)

    def _find_tree_node(self, session_id: str) -> SessionTreeNode | None:
        """Locate `session_id`'s node in the live subagent tree rooted at this process's
        top-level session, or `None` if it no longer exists (defensive only -- a session id
        handed back from a panel row or a just-tagged ask always names a node that still exists,
        since nothing in this tree is ever destroyed until its creator closes, per
        docs/specs/subagents.md's "Persistence" section)."""
        for node in walk_session_tree(self._session):
            if node.session.id == session_id:
                return node
        return None

    def _select_session(self, session_id: str) -> None:
        """Make `session_id` the currently displayed (sub)agent: swap `#history`/
        `#subagent-history` visibility, update the prompt input's disabled state, refresh the
        status bar's token tally, the header's model (and thinking-effort) display, and the
        `#session-name` line for the newly selected session, and refresh the panel's
        highlight/footer. A no-op if `session_id` doesn't name a live node (see
        `_find_tree_node`)."""
        node = self._find_tree_node(session_id)
        if node is None:
            return
        self._selected_session = node.session
        self._selected_handle = node.handle
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        subagent_history = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        if node.handle is None:
            subagent_history.display = False
            history.display = True
        else:
            self._render_full_subagent_transcript(node.session, node.handle)
            history.display = False
            subagent_history.display = True
        self._update_prompt_input_disabled_state()
        self._update_status_bar()
        self._refresh_header_title()
        self._update_session_name_line_for_selection()
        self._refresh_subagents_panel()

    def _update_session_name_line_for_selection(self) -> None:
        """Set the `#session-name` status line to the selected session's own title -- a
        subagent's `Session.name` is always set at creation (from `CreateSubagent`'s
        `session_title`, per docs/specs/subagents.md's "Subagent session model" section), so only
        the root session, before its first-turn naming classifier resolves, can still be `None`;
        that case falls back to the bare `NEW_SESSION_LABEL`, matching `compose()`'s own initial
        value (see `_update_session_name_line`'s docstring for why that case skips the
        `"Session: "` prefix)."""
        name = self._selected_session.name
        if name is not None:
            self._update_session_name_line(name)
        else:
            self.query_one(f"#{SESSION_NAME_ID}", Static).update(NEW_SESSION_LABEL)

    def _render_full_subagent_transcript(self, session: Session, handle: SubagentHandle) -> None:
        """Render a fresh, non-streaming snapshot of every message in `session.messages` into
        `#subagent-history`, replacing whatever was there before -- called only when `session`
        is newly selected (see `_select_session`); `_append_new_subagent_messages` handles
        catching the view up to new messages on later ticks while it stays selected. Always
        scrolls to the bottom and resets `_subagent_history_pinned_to_bottom` to `True`: a fresh
        selection should show the latest content regardless of where a *previous* selection's
        scroll happened to be left.
        """
        container = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        container.remove_children()
        self._subagent_transcript_notice = None
        self._mount_subagent_messages(container, session.messages)
        self._subagent_history_rendered_count = len(session.messages)
        self._mount_subagent_status_notice(container, handle)
        self._subagent_history_rendered_state = handle.state
        self._subagent_history_pinned_to_bottom = True
        container.scroll_end(animate=False)

    def _append_new_subagent_messages(self, session: Session, handle: SubagentHandle) -> None:
        """Catch `#subagent-history` up to `session.messages`/`handle.state` since the last
        render: mounts only messages added since then (mirroring how `#history` streams a live
        turn, rather than `_render_full_subagent_transcript`'s full rebuild) and re-mounts the
        trailing status notice if `handle.state` changed, so it flips from "still working" to
        "task complete" (or back, if `MessageSubagent` resumes it) without disturbing the rest of
        the view. Follows the bottom only if the view was already pinned there
        (`_subagent_history_pinned_to_bottom`, kept in sync by `_on_subagent_history_scroll_changed`)
        -- a user who scrolled up to reread earlier output isn't yanked back down. Called by
        `_tick_subagents_panel` every tick while a subagent is selected; a no-op once neither the
        message count nor the state has changed since the last call, so a dormant selection does
        no work between ticks.
        """
        new_message_count = len(session.messages) - self._subagent_history_rendered_count
        state_changed = handle.state != self._subagent_history_rendered_state
        if new_message_count <= 0 and not state_changed:
            return
        container = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        was_pinned = self._subagent_history_pinned_to_bottom
        if self._subagent_transcript_notice is not None:
            self._subagent_transcript_notice.remove()
            self._subagent_transcript_notice = None
        if new_message_count > 0:
            self._mount_subagent_messages(
                container, session.messages, start_index=self._subagent_history_rendered_count)
            self._subagent_history_rendered_count = len(session.messages)
        self._mount_subagent_status_notice(container, handle)
        self._subagent_history_rendered_state = handle.state
        self._scroll_if_pinned(container, was_pinned)
        self._update_status_bar()

    def _mount_subagent_messages(
        self, container: VerticalScroll, messages: list[ChatMessage], start_index: int = 0,
    ) -> None:
        """Mount `messages[start_index:]` into `container`, reusing `RenderingMixin`'s pure
        `_render_restored_tool_call`/`_render_tool_result` (safe against the root session's own
        `tool_registry`: a subagent's tool set is always a subset of every ancestor's, including
        the root's, by the Phase-1 intersection invariant -- see docs/specs/subagents.md's
        "Security model" section) rather than any of the `_mount_*_widget` helpers, which are
        hardwired to `#history` and to live streaming/status-bar bookkeeping this read-only view
        doesn't need. `responses_by_call_id` is built from the *full* `messages` list regardless
        of `start_index`, since a `tool_use` message in the mounted slice can pair with a
        `tool_response` message from earlier or later in the same slice. A `tool_use` message's
        own `content`, if non-empty, is rendered as a response block ahead of its tool calls -- it
        can carry commentary the model sent alongside those calls, including its final answer if
        that arrived in the same round as its last tool calls rather than a trailing text-only
        round (see `klorb.agents.policy._assistant_authored_text`, which relays exactly this text
        to the subagent's creator).
        """
        responses_by_call_id = {
            message.tool_call_id: message for message in messages
            if message.role == "tool_response" and message.tool_call_id is not None
        }
        for message in messages[start_index:]:
            if message.role == "user":
                display_content = strip_system_interjections(message.content)
                container.mount(Static(display_content, classes="prompt", markup=False))
            elif message.role == "assistant":
                text = message.content
                if message.processing_state == "aborted":
                    text = f"{text}\n\n*(interrupted)*"
                container.mount(Markdown(text, classes="response"))
            elif message.role == "thinking":
                text = resolve_thinking_body_text(message.content, message.reasoning_details)
                if message.processing_state == "aborted":
                    text = f"{text}\n\n(interrupted)"
                container.mount(Static(THINKING_LABEL, classes="thinking-label"))
                container.mount(Static(text, classes="thinking-body", markup=False))
                if message.reasoning_details:
                    reasoning_text = summarize_reasoning_details(message.reasoning_details)
                    if reasoning_text is not None:
                        container.mount(Static(REASONING_DETAILS_LABEL, classes="reasoning-details-label"))
                        container.mount(
                            Static(reasoning_text, classes="reasoning-details-body", markup=False))
            elif message.role == "tool_use":
                if message.content.strip():
                    container.mount(Markdown(message.content, classes="response"))
                for call in message.tool_calls or []:
                    rendered = self._render_restored_tool_call(call, responses_by_call_id.get(call.id))
                    container.mount(Static(TOOL_USE_LABEL, classes="tool-call-label"))
                    widget = ToolCallStatic(
                        rendered.summary_content, rendered.detail_content, rendered.on_click)
                    widget.set_detail_shown(self._tool_call_detail_shown)
                    container.mount(widget)

    def _mount_subagent_status_notice(self, container: VerticalScroll, handle: SubagentHandle) -> None:
        """Mount the trailing status `Static` as the last child of `container`, tracking it in
        `_subagent_transcript_notice` so the next render can remove it before mounting anything
        new (keeping it last). Text depends on `handle.state` plus two finer-grained cases:

        * While `"running"`: "Sending interrupt…" if an abort was just requested for this
          session (`_subagent_interrupt_pending`, set by `_note_subagent_interrupt_requested`
          and left in place until the handle actually finishes, so it survives every tick in
          between), else the ordinary "Subagent is still working…".
        * Once no longer `"running"`: "Subagent interrupted." if `handle.output` carries
          `SUBAGENT_ABORTED_MARKER` (`klorb.agents.policy._run_subagent_turn`'s own abort note --
          checked regardless of `_subagent_interrupt_pending`, so this reads correctly even after
          switching away and back), else the ordinary "Subagent task complete.". Clears
          `_subagent_interrupt_pending` for this session once reached, since the interrupt it was
          tracking has now resolved one way or another.
        """
        if handle.state == "running":
            if self._subagent_interrupt_pending == handle.session.id:
                text = _SUBAGENT_SENDING_INTERRUPT_NOTICE
            else:
                text = _SUBAGENT_STILL_RUNNING_NOTICE
        else:
            if self._subagent_interrupt_pending == handle.session.id:
                self._subagent_interrupt_pending = None
            aborted = handle.output is not None and SUBAGENT_ABORTED_MARKER in handle.output
            text = _SUBAGENT_INTERRUPTED_NOTICE if aborted else _SUBAGENT_TASK_COMPLETE_NOTICE
        notice = Static(text, classes="notice")
        container.mount(notice)
        self._subagent_transcript_notice = notice

    def _note_subagent_interrupt_requested(self, handle: SubagentHandle) -> None:
        """Immediately show "Sending interrupt…" in `#subagent-history` when Escape/Ctrl+C aborts
        the selected subagent's turn (`KeyActionsMixin._interrupt_running_activity`), so the user
        gets the same prompt confirmation the root session's own `_INTERRUPTING_MESSAGE` gives.
        The actual abort can take a moment to land (the subagent's background thread only notices
        `cancel_event` at its next stream/tool-call boundary) -- `_subagent_interrupt_pending`
        keeps `_mount_subagent_status_notice` showing this same text on every tick in between,
        rather than reverting to "still working"."""
        self._subagent_interrupt_pending = handle.session.id
        container = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        if self._subagent_transcript_notice is not None:
            self._subagent_transcript_notice.remove()
            self._subagent_transcript_notice = None
        was_pinned = self._subagent_history_pinned_to_bottom
        notice = Static(_SUBAGENT_SENDING_INTERRUPT_NOTICE, classes="notice")
        container.mount(notice)
        self._subagent_transcript_notice = notice
        self._scroll_if_pinned(container, was_pinned)

    def _on_subagent_history_scroll_changed(self) -> None:
        """Keep `_subagent_history_pinned_to_bottom` in sync with `#subagent-history`'s actual
        scroll position -- the subagent-transcript analog of `StatusBarMixin.
        _on_history_scroll_changed`, watched the same way (see `KeyActionsMixin.on_mount`)."""
        container = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        self._subagent_history_pinned_to_bottom = pinned_to_bottom(container)

    def _refresh_subagents_panel(self) -> None:
        """Rebuild the panel's rows from the live subagent tree and refresh the status-line
        fallback -- called after any selection change, panel toggle, or tick."""
        panel = self.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel)
        rows = [
            SubagentRowData(
                session_id=node.session.id, address=node.session.address(),
                title=node.session.name or NEW_SESSION_LABEL, role=node.session.config.role_name,
                state=node.handle.state if node.handle is not None else None)
            for node in walk_session_tree(self._session)
        ]
        panel.show_rows(
            rows, self._selected_session.id, frozenset(self._attention_needed), self._blink_phase)
        self._update_subagent_attention_status_line()

    def _update_subagent_attention_status_line(self) -> None:
        """Show the "Agent <address> needs your input" status-line fallback only while the panel
        itself is hidden and at least one session has an ask waiting on selection -- the panel's
        own blinking `(!)` marker already covers the case where it's visible. Picks the oldest
        (first-added) pending session when several are waiting at once, matching
        `_attention_needed`'s insertion order."""
        status = self.query_one(f"#{SUBAGENT_ATTENTION_STATUS_ID}", Static)
        if self._subagents_panel_shown or not self._attention_needed:
            status.display = False
            return
        oldest_id = next(iter(self._attention_needed))
        node = self._find_tree_node(oldest_id)
        address = node.session.address() if node is not None else "?"
        status.update(f"Agent {address} needs your input")
        status.display = True

    def _tick_subagents_panel(self) -> None:
        """`set_interval` callback (started by `_start_subagents_panel_timer`): flips the blink
        phase and catches the selected subagent's transcript up to any new messages or a state
        change (`_append_new_subagent_messages`, itself a no-op when neither happened) -- both
        regardless of whether the panel itself is currently shown, since the transcript view and
        the status-line attention fallback stay meaningful while it's hidden. Only refreshes the
        panel's own rows (`_refresh_subagents_panel`) while it's actually visible -- doing so
        while hidden would just repeatedly reset the `OptionList`'s highlight for no visible
        effect (see `on_option_list_option_highlighted`'s docstring)."""
        self._blink_phase = not self._blink_phase
        if self._selected_handle is not None:
            self._append_new_subagent_messages(self._selected_session, self._selected_handle)
        if self._subagents_panel_shown:
            self._refresh_subagents_panel()

    def _start_subagents_panel_timer(self) -> None:
        """Start `_tick_subagents_panel`'s recurring timer -- called once from `on_mount`
        (`KeyActionsMixin`, where the app's other `set_interval` timers are started)."""
        self.set_interval(_PANEL_TICK_INTERVAL_SECONDS, self._tick_subagents_panel)

    def _update_prompt_input_disabled_state(self) -> None:
        """Recompute the prompt input's `disabled` flag from whichever session is currently
        selected -- disabled whenever a subagent (not the root session) is selected, since "the
        user cannot communicate with subagents directly" (docs/plans/ready/021-subagents.md's
        "Prompt input" section). A no-op while `interaction-active` is set: an open permission/
        ask-user-questions/escalate-privileges panel (`InteractionsMixin._enter_interaction_mode`/
        `_exit_interaction_mode`) already owns the flag for its own duration, and by construction
        only ever shows for the currently-selected session anyway (see
        `_await_session_selected`), so there's nothing this method would change while it's up.
        """
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        if prompt_input.has_class("interaction-active"):
            return
        prompt_input.disabled = self._selected_session is not self._session

    async def _await_session_selected(self, session_id: str) -> None:
        """Block the calling coroutine until `session_id` is the currently selected session,
        polling rather than using a new synchronization primitive (see module docstring).
        Registers `session_id` in `_attention_needed` for the duration so the panel can blink a
        `(!)` marker next to it and the status-line fallback can announce it -- a no-op if
        `session_id` is already selected. Used by `InteractionsMixin`'s three `_confirm_*`
        methods, before they acquire `_interaction_lock`, so an ask for a session that isn't
        selected can't block the lock and starve every other panel (including the root's own).
        """
        if self._selected_session.id == session_id:
            return
        self._attention_needed.setdefault(session_id, None)
        self._refresh_subagents_panel()
        try:
            while self._selected_session.id != session_id:
                await asyncio.sleep(_ASK_GATE_POLL_INTERVAL_SECONDS)
        finally:
            self._attention_needed.pop(session_id, None)
            self._refresh_subagents_panel()
