# © Copyright 2026 Aaron Kimball
"""SubagentsPanelMixin: the Ctrl+G-toggled subagents panel, (sub)agent selection, the selected
subagent's transcript view, and the selection-gated ask-attention bookkeeping `InteractionsMixin`
polls."""

import asyncio

from textual.containers import VerticalScroll
from textual.widgets import OptionList, Static

from klorb.agents.runtime import SUBAGENT_ABORTED_MARKER, SessionTreeNode, SubagentHandle, walk_session_tree
from klorb.process_config import persist_sidebar
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
from klorb.tui.formatting import pinned_to_bottom
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.subagents_panel import (
    SUBAGENTS_LIST_ID,
    SubagentPanelOption,
    SubagentRowData,
    SubagentsPanel,
)
from klorb.tui.widgets.task_sidebar import TaskSidebar
from klorb.tui.widgets.tool_call_widgets import CrawlAnimatedStatic
from klorb.tui.widgets.virtualized_history import DEFAULT_CHUNK_SIZE_MESSAGES, VirtualizedHistoryContainer

_PANEL_TICK_INTERVAL_SECONDS = 0.6
"""How often `_tick_subagents_panel` fires: blinks the `(!)` attention marker and refreshes the
selected subagent's transcript (new messages, and the trailing status notice's text) so the view
doesn't look frozen mid-turn."""

_ASK_GATE_POLL_INTERVAL_SECONDS = 0.2
"""How often `_await_session_selected` re-checks whether its target session has become selected."""

_SUBAGENT_STILL_RUNNING_NOTICE = "Subagent is still working…"
_SUBAGENT_TASK_COMPLETE_NOTICE = "Subagent task complete."
_SUBAGENT_SENDING_INTERRUPT_NOTICE = "Sending interrupt…"
_SUBAGENT_INTERRUPTED_NOTICE = "Subagent interrupted."


class SubagentsPanelMixin(ReplAppBase):
    """Ctrl+G shows or hides a docked right-hand panel listing every session in the live
    subagent tree, with click/arrow-key row selection that switches the displayed transcript."""

    def action_toggle_subagents_panel(self) -> None:
        """Ctrl+G: show or hide the subagents panel. Mutually exclusive with the task sidebar
        since both dock the same right-hand slot."""
        panel = self.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel)
        if self._active_sidebar == "agents":
            self._active_sidebar = None
            panel.display = False
        else:
            if self._active_sidebar == "tasks":
                self.query_one(f"#{TASK_SIDEBAR_ID}", TaskSidebar).display = False
            self._active_sidebar = "agents"
            panel.display = True
            self._refresh_subagents_panel()
            self.query_one(f"#{SUBAGENTS_LIST_ID}", OptionList).focus()
        persist_sidebar(self._active_sidebar)
        if self._active_sidebar != "agents":
            self._update_subagent_attention_status_line()

    async def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Switch the displayed transcript as soon as a row is highlighted. A no-op when the
        highlighted row already names the selected session, preventing `_tick_subagents_panel`'s
        periodic refresh from re-triggering `_select_session` indefinitely."""
        if event.option_list.id != SUBAGENTS_LIST_ID:
            return
        option = event.option
        assert isinstance(option, SubagentPanelOption)
        if option.session_id == self._selected_session.id:
            return
        await self._select_session(option.session_id)

    def _find_tree_node(self, session_id: str) -> SessionTreeNode | None:
        """Locate `session_id`'s node in the live subagent tree rooted at this process's
        top-level session, or `None` if it no longer exists."""
        for node in walk_session_tree(self._session):
            if node.session.id == session_id:
                return node
        return None

    async def _select_session(self, session_id: str) -> None:
        """Make `session_id` the currently displayed (sub)agent. A no-op if `session_id`
        doesn't name a live node."""
        node = self._find_tree_node(session_id)
        if node is None:
            return
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        self._subagent_drafts[self._selected_session.id] = prompt_input.text
        self._selected_session = node.session
        self._selected_handle = node.handle
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        subagent_history = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        if node.handle is None:
            subagent_history.display = False
            history.display = True
        else:
            # `_render_full_subagent_transcript` measures `subagent_history`'s own layout, which
            # needs it visible already: a hidden (`display=False`) container measures as zero-sized.
            history.display = False
            subagent_history.display = True
            await self._render_full_subagent_transcript(node.session, node.handle)
        prompt_input.text = self._subagent_drafts.get(node.session.id, "")
        self._update_prompt_input_disabled_state()
        self._update_status_bar()
        self._refresh_header_title()
        self._update_session_name_line_for_selection()
        self._refresh_subagents_panel()

    def _update_session_name_line_for_selection(self) -> None:
        """Set the `#session-name` status line to the selected session's own title. Falls back
        to `NEW_SESSION_LABEL` when the name is `None`."""
        name = self._selected_session.name
        if name is not None:
            self._update_session_name_line(name)
        else:
            self.query_one(f"#{SESSION_NAME_ID}", Static).update(NEW_SESSION_LABEL)

    def _new_subagent_history_virtualizer(
        self, container: VerticalScroll, session: Session,
    ) -> VirtualizedHistoryContainer:
        """Build a `VirtualizedHistoryContainer` bound to `container` and `session`."""
        return VirtualizedHistoryContainer(
            container, lambda: len(session.messages),
            lambda start, end: self._render_message_range(
                session.messages[start:end], session.messages),
            self.call_after_refresh)

    async def _render_full_subagent_transcript(self, session: Session, handle: SubagentHandle) -> None:
        """Render a fresh snapshot of `session.messages` into `#subagent-history`, replacing
        whatever was there before; everything except the trailing `DEFAULT_CHUNK_SIZE_MESSAGES`
        messages starts collapsed behind a placeholder. Always scrolls to the bottom and resets
        `_subagent_history_pinned_to_bottom` to `True`."""
        container = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        container.remove_children()
        self._subagent_transcript_notice = None
        # `session.messages` returns a fresh copy on every access. Reading it once here into
        # `messages`, then reusing that copy for the mount and the rendered-count update below,
        # keeps a message appended by the background turn thread mid-render from being silently
        # skipped forever.
        messages = session.messages
        virtualizer = self._new_subagent_history_virtualizer(container, session)
        self._subagent_history_virtualizer = virtualizer
        trailing_start = await virtualizer.seed_collapsed_prefix(len(messages), DEFAULT_CHUNK_SIZE_MESSAGES)
        tail_widgets = self._render_message_range(messages[trailing_start:], messages)
        if tail_widgets:
            await container.mount(*tail_widgets)
        self._subagent_history_rendered_count = len(messages)
        self._mount_subagent_status_notice(container, handle)
        self._subagent_history_rendered_state = handle.state
        self._subagent_history_pinned_to_bottom = True
        virtualizer.force_layout()
        container.scroll_end(animate=False, immediate=True)

    def _append_new_subagent_messages(self, session: Session, handle: SubagentHandle) -> None:
        """Catch `#subagent-history` up to `session.messages`/`handle.state` since the last
        render, mounting only new messages and re-mounting the trailing status notice if
        `handle.state` changed. A no-op when neither the message count nor the state has
        changed."""
        # `session.messages` returns a fresh copy on every access. Reading it once into `messages`
        # and reusing it for the count check, the mount, and the rendered-count update keeps those
        # three steps from observing different lengths and silently dropping a message appended
        # in between.
        messages = session.messages
        new_message_count = len(messages) - self._subagent_history_rendered_count
        state_changed = handle.state != self._subagent_history_rendered_state
        if new_message_count <= 0 and not state_changed:
            return
        container = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        was_pinned = self._subagent_history_pinned_to_bottom
        self._remove_subagent_transcript_notice()
        if new_message_count > 0:
            start = self._subagent_history_rendered_count
            widgets = self._render_message_range(messages[start:], messages)
            if widgets:
                container.mount(*widgets)
            assert self._subagent_history_virtualizer is not None
            self._subagent_history_virtualizer.register_settled_range(start, len(messages), widgets)
            self._subagent_history_rendered_count = len(messages)
        self._mount_subagent_status_notice(container, handle)
        self._subagent_history_rendered_state = handle.state
        self._scroll_if_pinned(container, was_pinned)
        self._update_status_bar()

    def _mount_subagent_status_notice(self, container: VerticalScroll, handle: SubagentHandle) -> None:
        """Mount the trailing status `Static` as the last child of `container`, tracking it in
        `_subagent_transcript_notice` so the next render can remove it before mounting anything
        new. Text depends on `handle.state`: "Sending interrupt…" if an abort was requested,
        "Subagent interrupted." if the handle carries `SUBAGENT_ABORTED_MARKER`, "Subagent task
        complete." if it completed normally, or "Subagent is still working…" while running."""
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
        notice = self._build_subagent_notice(text)
        container.mount(notice)
        self._subagent_transcript_notice = notice

    def _build_subagent_notice(self, text: str) -> Static:
        """Build the trailing status notice widget for `text`: a `CrawlAnimatedStatic` with a
        left-to-right crawl highlight for the "still working" notice, so the user can see the
        subagent hasn't frozen, else a plain `Static`."""
        if text == _SUBAGENT_STILL_RUNNING_NOTICE:
            return CrawlAnimatedStatic(text, classes="notice")
        return Static(text, classes="notice")

    def _remove_subagent_transcript_notice(self) -> None:
        """Unmount `_subagent_transcript_notice`, stopping its crawl-animation timer first if
        it's a `CrawlAnimatedStatic`. A no-op if nothing is currently mounted."""
        notice = self._subagent_transcript_notice
        if notice is None:
            return
        if isinstance(notice, CrawlAnimatedStatic):
            notice.remove_self()
        else:
            notice.remove()
        self._subagent_transcript_notice = None

    def _note_subagent_interrupt_requested(self, handle: SubagentHandle) -> None:
        """Immediately show "Sending interrupt…" in `#subagent-history` when Escape/Ctrl+C
        aborts the selected subagent's turn. `_subagent_interrupt_pending` keeps
        `_mount_subagent_status_notice` showing this same text on every tick until the abort
        lands."""
        self._subagent_interrupt_pending = handle.session.id
        container = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        self._remove_subagent_transcript_notice()
        was_pinned = self._subagent_history_pinned_to_bottom
        notice = Static(_SUBAGENT_SENDING_INTERRUPT_NOTICE, classes="notice")
        container.mount(notice)
        self._subagent_transcript_notice = notice
        self._scroll_if_pinned(container, was_pinned)

    async def _on_subagent_history_scroll_changed(self) -> None:
        """Keep `_subagent_history_pinned_to_bottom` in sync with `#subagent-history`'s actual
        scroll position, and collapse/expand chunks for the new viewport. A no-op once the app
        has started shutting down, since this can still fire after the container itself is
        gone."""
        if not self.is_running:
            return
        container = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        self._subagent_history_pinned_to_bottom = pinned_to_bottom(container)
        if self._subagent_history_virtualizer is not None:
            await self._subagent_history_virtualizer.refresh_visibility()

    def _refresh_subagents_panel(self) -> None:
        """Rebuild the panel's rows from the live subagent tree and refresh the status-line
        fallback."""
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
        itself is hidden and at least one session has an ask waiting on selection. Picks the
        oldest pending session when several are waiting at once."""
        status = self.query_one(f"#{SUBAGENT_ATTENTION_STATUS_ID}", Static)
        if self._active_sidebar == "agents" or not self._attention_needed:
            status.display = False
            return
        oldest_id = next(iter(self._attention_needed))
        node = self._find_tree_node(oldest_id)
        address = node.session.address() if node is not None else "?"
        status.update(f"Agent {address} needs your input")
        status.display = True

    def _tick_subagents_panel(self) -> None:
        """`set_interval` callback: flips the blink phase and catches the selected subagent's
        transcript up to any new messages or a state change. Only refreshes the panel's own
        rows while it's actually visible."""
        self._blink_phase = not self._blink_phase
        if self._selected_handle is not None:
            # Re-resolve the handle from the tree before using it: `register()` replaces the
            # tracker's entry for this session on every resume (a direct message or
            # SendMessage), so a `_selected_handle` cached from selection time can point at
            # an orphaned, permanently-"running" object whose `state` never again matches the
            # live turn.
            node = self._find_tree_node(self._selected_session.id)
            if node is not None and node.handle is not None:
                self._selected_handle = node.handle
            self._append_new_subagent_messages(self._selected_session, self._selected_handle)
        if self._active_sidebar == "agents":
            self._refresh_subagents_panel()

    def _start_subagents_panel_timer(self) -> None:
        """Start `_tick_subagents_panel`'s recurring timer."""
        self.set_interval(_PANEL_TICK_INTERVAL_SECONDS, self._tick_subagents_panel)

    def _update_prompt_input_disabled_state(self) -> None:
        """Recompute the prompt input's `disabled` flag. A no-op while `interaction-active`
        is set."""
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        if prompt_input.has_class("interaction-active"):
            return
        prompt_input.disabled = False

    async def _await_session_selected(self, session_id: str) -> None:
        """Block the calling coroutine until `session_id` is the currently selected session.
        Registers `session_id` in `_attention_needed` for the duration so the panel can blink a
        `(!)` marker and the status-line fallback can announce it. A no-op if `session_id` is
        already selected."""
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
