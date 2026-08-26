# © Copyright 2026 Aaron Kimball
"""SubagentsPanelMixin: the Ctrl+G-toggled subagents panel, (sub)agent selection, the selected
subagent's transcript view, and the selection-gated ask-attention bookkeeping `InteractionsMixin`
polls."""

import asyncio

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import OptionList, Static

from klorb.agents.chat import CHAT_USER_ID, MENTION_TOKEN_RE, ChatMessage, chat_nickname, live_mention_targets
from klorb.agents.policy import notify_chat_mention
from klorb.agents.runtime import SUBAGENT_ABORTED_MARKER, SessionTreeNode, SubagentHandle, walk_session_tree
from klorb.process_config import persist_sidebar
from klorb.session import Session
from klorb.tui._base import ReplAppBase
from klorb.tui.constants import (
    CHAT_HISTORY_ID,
    CHAT_ROW_ID,
    HISTORY_ID,
    NEW_SESSION_LABEL,
    PROMPT_INPUT_ID,
    SESSION_NAME_ID,
    SUBAGENT_ATTENTION_STATUS_ID,
    SUBAGENT_HISTORY_ID,
    SUBAGENTS_PANEL_ID,
)
from klorb.tui.formatting import pinned_to_bottom
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.subagents_panel import (
    SUBAGENTS_LIST_ID,
    ChatRowMarker,
    SubagentRowData,
    SubagentsPanel,
)
from klorb.tui.widgets.tool_call_widgets import CrawlAnimatedStatic
from klorb.tui.widgets.virtualized_history import DEFAULT_CHUNK_SIZE_MESSAGES, VirtualizedHistoryContainer

_CHAT_ATTENTION_KEY = "chat"
"""Synthetic `ReplApp._attention_needed` key for unread chat while `_chat_selected` is `False`."""

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
        """Ctrl+G: show or hide the subagents panel, mutually exclusive with the other sidebar
        panels."""
        panel = self.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel)
        if self._active_sidebar == "agents":
            self._active_sidebar = None
            panel.display = False
        else:
            self._show_subagents_panel(panel)
            self._refresh_subagents_panel()
            self.query_one(f"#{SUBAGENTS_LIST_ID}", OptionList).focus()
        persist_sidebar(self._active_sidebar)
        if self._active_sidebar != "agents":
            self._update_subagent_attention_status_line()

    def _show_subagents_panel(self, panel: SubagentsPanel) -> None:
        """Make the subagents panel the active right-hand sidebar."""
        self._hide_other_sidebars("agents")
        self._active_sidebar = "agents"
        panel.display = True

    async def action_open_chat_room(self) -> None:
        """Ctrl+B: open the subagents panel (if hidden) and select its chat room row directly,
        in one step."""
        panel = self.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel)
        if self._active_sidebar != "agents":
            self._show_subagents_panel(panel)
            persist_sidebar(self._active_sidebar)
        await self._select_chat()
        self.query_one(f"#{SUBAGENTS_LIST_ID}", OptionList).focus()

    async def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Switch the displayed transcript as soon as a row is highlighted. A no-op when the
        highlighted row already names the current selection, preventing `_tick_subagents_panel`'s
        periodic refresh from re-triggering `_select_session`/`_select_chat` indefinitely."""
        if event.option_list.id != SUBAGENTS_LIST_ID:
            return
        option_id = event.option.id
        assert option_id is not None
        if option_id == CHAT_ROW_ID:
            await self._select_chat()
            return
        if option_id == self._selected_session.id and not self._chat_selected:
            return
        await self._select_session(option_id)

    def _find_tree_node(self, session_id: str) -> SessionTreeNode | None:
        """Locate `session_id`'s node in the live subagent tree rooted at this process's
        top-level session, or `None` if it no longer exists."""
        for node in walk_session_tree(self._session):
            if node.session.id == session_id:
                return node
        return None

    def _current_draft_key(self) -> str:
        """The key `_subagent_drafts` currently saves/restores unsent prompt-input text under:
        `CHAT_ROW_ID` while the chat room is selected, else the selected session's own id."""
        return CHAT_ROW_ID if self._chat_selected else self._selected_session.id

    async def _select_session(self, session_id: str) -> None:
        """Make `session_id` the currently displayed (sub)agent, leaving the chat room. A no-op
        if `session_id` doesn't name a live node."""
        node = self._find_tree_node(session_id)
        if node is None:
            return
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        self._subagent_drafts[self._current_draft_key()] = prompt_input.text
        self._chat_selected = False
        self._selected_session = node.session
        self._selected_handle = node.handle
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        subagent_history = self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll)
        self.query_one(f"#{CHAT_HISTORY_ID}", VerticalScroll).display = False
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

    async def _select_chat(self) -> None:
        """Make the chat room the currently displayed view. A no-op if it's already selected."""
        if self._chat_selected:
            return
        # Seeds the user's own hwm at "now" the first time they ever view the chat room.
        self._session.chat_channel.register_participant(CHAT_USER_ID)
        prompt_input = self.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        self._subagent_drafts[self._current_draft_key()] = prompt_input.text
        self._chat_selected = True
        self.query_one(f"#{HISTORY_ID}", VerticalScroll).display = False
        self.query_one(f"#{SUBAGENT_HISTORY_ID}", VerticalScroll).display = False
        # `_render_full_chat_transcript` measures `chat_history`'s own layout, which needs it
        # visible already: a hidden (`display=False`) container measures as zero-sized.
        self.query_one(f"#{CHAT_HISTORY_ID}", VerticalScroll).display = True
        await self._render_full_chat_transcript()
        prompt_input.text = self._subagent_drafts.get(CHAT_ROW_ID, "")
        self._update_prompt_input_disabled_state()
        self._update_status_bar()
        self._refresh_header_title()
        self._update_session_name_line_for_selection()
        self._refresh_subagents_panel()

    def _update_session_name_line_for_selection(self) -> None:
        """Set the `#session-name` status line to the chat room's own label, or the selected
        session's title, falling back to `NEW_SESSION_LABEL` when that title is `None`."""
        if self._chat_selected:
            self._update_session_name_line("Chat Room")
            return
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
        self._subagent_transcript_render_in_flight = True
        try:
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
            trailing_start = await virtualizer.seed_collapsed_prefix(
                len(messages), DEFAULT_CHUNK_SIZE_MESSAGES)
            tail_widgets = self._render_message_range(messages[trailing_start:], messages)
            if tail_widgets:
                await container.mount(*tail_widgets)
            self._subagent_history_rendered_count = len(messages)
            self._mount_subagent_status_notice(container, handle)
            self._subagent_history_rendered_state = handle.state
            self._subagent_history_pinned_to_bottom = True
            virtualizer.force_layout()
            container.scroll_end(animate=False, immediate=True)
        finally:
            self._subagent_transcript_render_in_flight = False

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
        """Rebuild the panel's rows (including the synthetic chat row) from the live subagent
        tree and refresh the status-line fallback."""
        panel = self.query_one(f"#{SUBAGENTS_PANEL_ID}", SubagentsPanel)
        rows = [
            SubagentRowData(
                session_id=node.session.id, address=node.session.address(),
                title=node.session.name or NEW_SESSION_LABEL, role=node.session.config.role_name,
                state=node.handle.state if node.handle is not None else None)
            for node in walk_session_tree(self._session)
        ]
        panel.show_rows(
            rows, self._selected_session.id, frozenset(self._attention_needed), self._blink_phase,
            chat_selected=self._chat_selected, chat_marker=self._current_chat_marker())
        self._update_subagent_attention_status_line()

    def _current_chat_marker(self) -> ChatRowMarker:
        """The chat room's own unread state for the panel row's marker: `"none"` while it's the
        current selection, else `"mention"`/`"unread"`/`"none"` from the user's own unread
        counts."""
        if self._chat_selected:
            return "none"
        channel = self._session.chat_channel
        if channel.unread_mention_count(CHAT_USER_ID) > 0:
            return "mention"
        if channel.unread_count(CHAT_USER_ID) > 0:
            return "unread"
        return "none"

    def _sync_chat_attention(self) -> None:
        """Keep `_CHAT_ATTENTION_KEY`'s membership in `_attention_needed` matching whether the
        chat room currently has unread messages the user isn't looking at, so the status-line
        fallback (`_update_subagent_attention_status_line`) picks it up like any other pending
        ask."""
        if self._current_chat_marker() != "none":
            self._attention_needed.setdefault(_CHAT_ATTENTION_KEY, None)
        else:
            self._attention_needed.pop(_CHAT_ATTENTION_KEY, None)

    def _update_subagent_attention_status_line(self) -> None:
        """Show the status-line fallback only while the panel itself is hidden and something
        needs the user's attention."""
        status = self.query_one(f"#{SUBAGENT_ATTENTION_STATUS_ID}", Static)
        if self._active_sidebar == "agents" or not self._attention_needed:
            status.display = False
            return
        oldest_id = next(iter(self._attention_needed))
        if oldest_id == _CHAT_ATTENTION_KEY:
            mentioned = self._session.chat_channel.unread_mention_count(CHAT_USER_ID) > 0
            status.update("Chat room: you were mentioned" if mentioned else "Chat room has new messages")
            status.display = True
            return
        node = self._find_tree_node(oldest_id)
        address = node.session.address() if node is not None else "?"
        status.update(f"Agent {address} needs your input")
        status.display = True

    def _tick_subagents_panel(self) -> None:
        """`set_interval` callback: flips the blink phase, catches the selected subagent's or
        chat room's transcript up to any new messages, and keeps the chat attention state
        current."""
        self._blink_phase = not self._blink_phase
        if self._chat_selected:
            self._append_new_chat_messages()
        elif self._selected_handle is not None and not self._subagent_transcript_render_in_flight:
            # Skip the catch-up while `_render_full_subagent_transcript` is mid-rebuild of the
            # same container, since this timer callback runs on its own asyncio task and can
            # interleave between that coroutine's `await` points; the next tick catches back up
            # once it's done.
            # Re-resolve the handle from the tree before using it: `register()` replaces the
            # tracker's entry for this session on every resume (a direct message or
            # SendMessage), so a `_selected_handle` cached from selection time can point at
            # an orphaned, permanently-"running" object whose `state` never again matches the
            # live turn.
            node = self._find_tree_node(self._selected_session.id)
            if node is not None and node.handle is not None:
                self._selected_handle = node.handle
            self._append_new_subagent_messages(self._selected_session, self._selected_handle)
        self._sync_chat_attention()
        if self._active_sidebar == "agents":
            self._refresh_subagents_panel()
        else:
            self._update_subagent_attention_status_line()

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

    def _chat_display_name(self, participant_id: str) -> str:
        """The TUI's own display form for a chat participant: `"You"` for the user, else
        `chat_nickname()` for whichever live session (if any) `participant_id` names."""
        if participant_id == CHAT_USER_ID:
            return "You"
        node = self._find_tree_node(participant_id)
        return chat_nickname(node.session) if node is not None else chat_nickname(participant_id)

    def _build_chat_message_content(self, message: ChatMessage) -> Text:
        """Build `[HH:MM] <sender>: <body>` for one chat message, with every `@mention` inside
        `body` substituted with its display nickname and rendered in bold, resolved fresh
        against the live session tree."""
        sender = self._chat_display_name(message.sender_id)
        text = Text(f"[{message.timestamp.strftime('%H:%M')}] {sender}: ")
        targets = live_mention_targets(self._session)
        body = message.body
        last_end = 0
        for match in MENTION_TOKEN_RE.finditer(body):
            resolved_id = targets.get(match.group(1))
            text.append(body[last_end:match.start()])
            if resolved_id is not None:
                text.append(f"@{self._chat_display_name(resolved_id)}", style="bold")
            else:
                text.append(match.group(0))
            last_end = match.end()
        text.append(body[last_end:])
        return text

    def _render_chat_message_widget(self, message: ChatMessage) -> Static:
        """Render one chat message as a `Static`, styled distinctly if the user posted it."""
        classes = "chat-message-own" if message.sender_id == CHAT_USER_ID else "chat-message"
        return Static(self._build_chat_message_content(message), classes=classes)

    async def _render_full_chat_transcript(self) -> None:
        """Render every retained chat message into `#chat-history`, replacing whatever was
        there before, and scroll to the bottom."""
        container = self.query_one(f"#{CHAT_HISTORY_ID}", VerticalScroll)
        container.remove_children()
        messages = self._session.chat_channel.history()
        widgets = [self._render_chat_message_widget(message) for message in messages]
        if widgets:
            await container.mount(*widgets)
        self._chat_history_rendered_count = len(messages)
        self._chat_history_pinned_to_bottom = True
        container.scroll_end(animate=False, immediate=True)

    def _append_new_chat_messages(self) -> None:
        """Catch `#chat-history` up to the channel's retained log since the last render,
        mounting only the messages added since. A no-op when nothing new has arrived."""
        messages = self._session.chat_channel.history()
        new_count = len(messages) - self._chat_history_rendered_count
        if new_count <= 0:
            return
        container = self.query_one(f"#{CHAT_HISTORY_ID}", VerticalScroll)
        was_pinned = self._chat_history_pinned_to_bottom
        widgets = [
            self._render_chat_message_widget(message)
            for message in messages[self._chat_history_rendered_count:]
        ]
        if widgets:
            container.mount(*widgets)
        self._chat_history_rendered_count = len(messages)
        self._scroll_if_pinned(container, was_pinned)

    async def _on_chat_history_scroll_changed(self) -> None:
        """Keep `_chat_history_pinned_to_bottom` in sync with `#chat-history`'s actual scroll
        position. A no-op once the app has started shutting down, since this can still fire
        after the container itself is gone."""
        if not self.is_running:
            return
        container = self.query_one(f"#{CHAT_HISTORY_ID}", VerticalScroll)
        self._chat_history_pinned_to_bottom = pinned_to_bottom(container)

    def _submit_chat_post(self, prompt_text: str) -> None:
        """Post `prompt_text` to the chat room as the user, attempting an active `@mention` wake
        for each resolved mention."""
        channel = self._session.chat_channel
        message = channel.post(CHAT_USER_ID, prompt_text, self._session)
        for mentioned_id in message.mentions:
            notify_chat_mention(self._process_config, channel, self._session, mentioned_id)
        self._append_new_chat_messages()
        self._refresh_subagents_panel()
