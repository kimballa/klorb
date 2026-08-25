# © Copyright 2026 Aaron Kimball
"""RenderingMixin: mounting and updating response/thinking/tool-call widgets in the
history for ReplApp."""

import json
import logging
from typing import Any

from textual.containers import VerticalScroll
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Markdown, Static

from klorb.message import Message as ChatMessage
from klorb.message import ToolCallRequest
from klorb.session import ToolCallEvent
from klorb.tools.registry import NoSuchToolException
from klorb.tools.tool import (
    ReadPreview,
    default_invalid_tool_call_detail,
    default_invalid_tool_call_summary,
    default_tool_call_detail,
    default_tool_call_summary,
)
from klorb.tools.util import FullFileView
from klorb.tui._base import ReplAppBase
from klorb.tui.constants import HISTORY_ID
from klorb.tui.formatting import (
    extract_skill_activation_notice,
    prefix_with_header,
    render_diff_content,
    render_full_file_content,
    render_read_preview_content,
    resolve_thinking_body_text,
    strip_system_interjections,
    summarize_reasoning_details,
)
from klorb.tui.panels.preview_screens import DiffDetailScreen, ReadDetailScreen
from klorb.tui.widgets.tool_call_widgets import (
    RenderedToolCall,
    RunningToolCallStatic,
    ToolCallStatic,
    TurnWaitingStatic,
)
from klorb.tui.widgets.virtualized_history import DEFAULT_CHUNK_SIZE_MESSAGES, VirtualizedHistoryContainer

logger = logging.getLogger(__name__)

THINKING_LABEL = "<Thinking>"
REASONING_DETAILS_LABEL = "<Reasoning>"
TOOL_USE_LABEL = "<Tool use>"

_DIFF_PREVIEW_MAX_LINES = 8
"""How many diff lines `RenderedToolCall.summary_content` shows inline before truncating."""


class RenderingMixin(ReplAppBase):
    """Response, thinking, and tool-call rendering/mounting into the history scroll."""

    def _new_history_virtualizer(self, history: VerticalScroll) -> VirtualizedHistoryContainer:
        """Build a `VirtualizedHistoryContainer` bound to `history` and this app's currently
        active session."""
        return VirtualizedHistoryContainer(
            history, lambda: len(self._session.messages),
            lambda start, end: self._render_message_range(
                self._session.messages[start:end], self._session.messages),
            self.call_after_refresh)

    def _mount_response_widget(self, initial_text: str) -> Markdown:
        """Mount a new `Markdown` widget for a streaming response and return it."""
        self._clear_turn_waiting_widget()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Markdown(initial_text, classes="response")
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        return widget

    def _update_response_widget(self, widget: Markdown, text: str) -> None:
        """Update a streaming response `Markdown` widget with the latest accumulated `text`."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()

    def _mount_thinking_widget(self, initial_text: str) -> tuple[Static, Static]:
        """Mount a left-justified `<Thinking>` label followed by an italicized `Static`
        widget for a streaming thinking block, and return `(body_widget, label_widget)`."""
        self._clear_turn_waiting_widget()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        label_widget = Static(THINKING_LABEL, classes="thinking-label")
        history.mount(label_widget)
        widget = Static(initial_text, classes="thinking-body", markup=False)
        history.mount(widget)
        self._update_status_bar()
        # A folded `<Thinking>` block hides its own liveness signal, so re-trail the waiting
        # notice under it rather than leaving the turn looking finished.
        self._turn_waiting_widget = self._mount_turn_waiting_widget()
        return widget, label_widget

    def _update_thinking_widget(self, widget: Static, text: str) -> None:
        """Update a streaming `<Thinking>` `Static` widget with the latest accumulated `text`."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()

    def _mount_reasoning_details_widget(self, text: str) -> tuple[Static, Static]:
        """Mount a left-justified `<Reasoning>` label followed by an italicized `Static`
        widget showing `text`, and return `(body_widget, label_widget)`."""
        self._clear_turn_waiting_widget()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        label_widget = Static(REASONING_DETAILS_LABEL, classes="reasoning-details-label")
        history.mount(label_widget)
        widget = Static(text, classes="reasoning-details-body", markup=False)
        history.mount(widget)
        self._update_status_bar()
        # Its content is just a static placeholder count, not a live signal, so re-trail the
        # waiting notice under it rather than leaving the turn looking finished.
        self._turn_waiting_widget = self._mount_turn_waiting_widget()
        return widget, label_widget

    def _update_reasoning_details_widget(self, widget: Static, text: str) -> None:
        """Update a `<Reasoning>` `Static` widget with the latest `text`."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()

    def _render_tool_call(self, event: ToolCallEvent) -> RenderedToolCall:
        """Render `event` as a `RenderedToolCall`."""
        return self._render_tool_result(
            event.name, event.args, event.result, event.error, event.raw_arguments)

    def _render_tool_result(
        self, name: str, args: dict[str, Any], result: Any, error: str | None,
        raw_arguments: str | None,
    ) -> RenderedToolCall:
        """Render one finished tool call as a `RenderedToolCall`."""
        if raw_arguments is not None:
            assert error is not None
            return RenderedToolCall(
                summary_content=default_invalid_tool_call_summary(name, error),
                detail_content=default_invalid_tool_call_detail(name, raw_arguments, error))
        registry = self._session.tool_registry
        try:
            if registry is None:
                raise KeyError(name)
            tool = registry.instantiate_tool(name)
        except (KeyError, NoSuchToolException):
            return RenderedToolCall(
                summary_content=default_tool_call_summary(name, args, error),
                detail_content=default_tool_call_detail(name, args, result, error))

        diff_preview = tool.diff_preview(args, result, error)
        if diff_preview is not None:
            # `full_diff_content` is reused bare (no header) for the overlay's body, since
            # DiffDetailScreen already shows `diff_preview.label` as its own separate header
            # Static -- prefixing it in here too would show it twice there.
            full_diff_content = render_diff_content(diff_preview.hunks, max_lines=None)
            compact_diff_content = render_diff_content(
                diff_preview.hunks, max_lines=_DIFF_PREVIEW_MAX_LINES)
            return RenderedToolCall(
                summary_content=prefix_with_header(diff_preview.label, compact_diff_content),
                detail_content=prefix_with_header(diff_preview.label, full_diff_content),
                on_click=lambda: self._open_diff_detail_screen(diff_preview.label, full_diff_content))

        read_preview = tool.read_preview(args, result, error)
        if read_preview is not None:
            compact_read_content = render_read_preview_content(
                read_preview.preview_lines, read_preview.truncated)
            return RenderedToolCall(
                summary_content=prefix_with_header(read_preview.label, compact_read_content),
                detail_content=tool.detail_view(args, result, error),
                on_click=lambda: self._open_read_detail_screen(read_preview))

        return RenderedToolCall(
            summary_content=tool.summary(args, result, error),
            detail_content=tool.detail_view(args, result, error))

    def _open_diff_detail_screen(self, label: str, content: Content) -> None:
        """Push `DiffDetailScreen` showing `content`."""
        self.push_screen(DiffDetailScreen(label, content))

    def _open_read_detail_screen(self, preview: ReadPreview) -> None:
        """Perform `preview`'s lazy full-subject read and push `ReadDetailScreen` with the
        result, or show an in-overlay error if the read fails."""
        try:
            full_view = preview.open_full()
        except Exception as exc:
            full_view = FullFileView(lines=None, error=str(exc), scroll_to_line=1)
        if full_view.lines is None:
            content: Content = Content(f"Could not reopen: {full_view.error}")
        else:
            content = render_full_file_content(full_view.lines)
        self.push_screen(
            ReadDetailScreen(preview.label, content, scroll_to_line=full_view.scroll_to_line))

    def _build_tool_call_widget(self, rendered: RenderedToolCall) -> tuple[ToolCallStatic, Static]:
        """Build (without mounting) a `<Tool use>` label followed by a `ToolCallStatic` for one
        finished tool call, and return `(widget, label_widget)`. Applies the current
        detail-shown state and tracks `widget` in `_tool_call_widgets` for the Ctrl+O toggle."""
        label_widget = Static(TOOL_USE_LABEL, classes="tool-call-label")
        widget = ToolCallStatic(rendered.summary_content, rendered.detail_content, rendered.on_click)
        widget.set_detail_shown(self._tool_call_detail_shown)
        self._tool_call_widgets.add(widget)
        return widget, label_widget

    def _mount_tool_call_widget(self, rendered: RenderedToolCall) -> tuple[ToolCallStatic, Static]:
        """Build and mount a `<Tool use>` label followed by a `ToolCallStatic` for one finished
        tool call, and return `(widget, label_widget)`. Refreshes the status bar to include the
        tool response's token count."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget, label_widget = self._build_tool_call_widget(rendered)
        history.mount(label_widget)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        return widget, label_widget

    def _render_tool_call_summary(self, name: str, args: dict[str, Any]) -> str:
        """Render a tool call's pre-execution summary by calling `Tool.summary(args)` with no
        `result` or `error`. Falls back to the module-level `default_tool_call_summary()` if
        the tool name isn't registered."""
        registry = self._session.tool_registry
        try:
            if registry is None:
                raise KeyError(name)
            tool = registry.instantiate_tool(name)
        except (KeyError, NoSuchToolException):
            return default_tool_call_summary(name, args, None)
        return tool.summary(args)

    def _mount_running_tool_call_widget(
        self, call_id: str, summary_text: str,
    ) -> RunningToolCallStatic:
        """Mount a `<Tool use>` label followed by a `RunningToolCallStatic` with a crawling
        animation. Stores the widget keyed by `call_id` so the completion callback can
        finalize it in place rather than mounting a duplicate. Tracks it in
        `_tool_call_widgets` for the Ctrl+O detail toggle."""
        self._clear_turn_waiting_widget()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(TOOL_USE_LABEL, classes="tool-call-label")
        history.mount(label_widget)
        widget = RunningToolCallStatic(summary_text)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._tool_call_widgets.add(widget)
        self._running_tool_call_widgets[call_id] = widget
        self._update_status_bar()
        return widget

    def _mount_turn_waiting_widget(self) -> TurnWaitingStatic:
        """Mount a `TurnWaitingStatic` into history showing an animated "still working" notice."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = TurnWaitingStatic()
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _clear_turn_waiting_widget(self) -> None:
        """Remove `self._turn_waiting_widget` (if it's still mounted) and stop its timer."""
        if self._turn_waiting_widget is None:
            return
        widget = self._turn_waiting_widget
        self._turn_waiting_widget = None
        widget.remove_self()

    def _running_tool_call_anchor(self) -> Static | None:
        """The `<Tool use>` label widget mounted just above the currently-running tool call,
        if any. Tool calls run serially, so at most one is un-finalized at a time; this returns
        that widget's immediately-preceding `.tool-call-label` sibling, and `None` when nothing
        is running."""
        if not self._running_tool_call_widgets:
            return None
        widget = list(self._running_tool_call_widgets.values())[-1]
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        children = list(history.children)
        try:
            index = children.index(widget)
        except ValueError:
            return None
        label = children[index - 1] if index > 0 else None
        if isinstance(label, Static) and label.has_class("tool-call-label"):
            return label
        return widget

    def _finalize_running_tool_call_widget(
        self, widget: RunningToolCallStatic, rendered: RenderedToolCall,
    ) -> None:
        """Stop the running indicator animation and replace it with the final
        summary/detail content."""
        widget.finalize(rendered.summary_content, rendered.detail_content, rendered.on_click)
        widget.set_detail_shown(self._tool_call_detail_shown)
        self._update_status_bar()

    def _render_message_range(
        self, messages: list[ChatMessage], response_lookup: list[ChatMessage] | None = None,
    ) -> list[Widget]:
        """Build (without mounting) the widgets a live turn or `_mount_restored_history` would
        show for `messages`. `response_lookup` resolves each `tool_use` message's tool response
        by call id, defaulting to `messages` itself."""
        lookup_messages = response_lookup if response_lookup is not None else messages
        responses_by_call_id = {
            message.tool_call_id: message for message in lookup_messages
            if message.role == "tool_response" and message.tool_call_id is not None
        }
        widgets: list[Widget] = []
        for message in messages:
            if message.role == "user":
                display_content = strip_system_interjections(message.content)
                widgets.append(Static(display_content, classes="prompt", markup=False))
                skill_notice = extract_skill_activation_notice(message.content)
                if skill_notice is not None:
                    widgets.append(Static(skill_notice, classes="notice", markup=False))
            elif message.role == "assistant":
                text = message.content
                if message.processing_state == "aborted":
                    text = f"{text}\n\n*(interrupted)*"
                widgets.append(Markdown(text, classes="response"))
            elif message.role == "thinking":
                text = resolve_thinking_body_text(message.content, message.reasoning_details)
                if message.processing_state == "aborted":
                    text = f"{text}\n\n(interrupted)"
                widgets.append(Static(THINKING_LABEL, classes="thinking-label"))
                widgets.append(Static(text, classes="thinking-body", markup=False))
                if message.reasoning_details:
                    reasoning_details_text = summarize_reasoning_details(message.reasoning_details)
                    if reasoning_details_text is not None:
                        widgets.append(Static(REASONING_DETAILS_LABEL, classes="reasoning-details-label"))
                        widgets.append(
                            Static(reasoning_details_text, classes="reasoning-details-body", markup=False))
            elif message.role == "tool_use":
                if message.content.strip():
                    # A `tool_use`-role message can carry commentary alongside the tool calls it
                    # requested (`Session._send_and_receive` sets `content` before reclassifying
                    # the message to `tool_use`) -- e.g. the model's final answer, if that answer
                    # came in the same round as its last tool calls rather than a trailing
                    # text-only round.
                    widgets.append(Markdown(message.content, classes="response"))
                for call in message.tool_calls or []:
                    rendered = self._render_restored_tool_call(
                        call, responses_by_call_id.get(call.id))
                    tool_widget, label_widget = self._build_tool_call_widget(rendered)
                    widgets.append(label_widget)
                    widgets.append(tool_widget)
        return widgets

    async def _mount_restored_history(self, messages: list[ChatMessage]) -> None:
        """Re-render `messages` into the history scroll so a restored conversation reads the
        same way it would have live. Everything before the trailing
        `DEFAULT_CHUNK_SIZE_MESSAGES` messages starts collapsed behind a placeholder, so
        opening a long saved session stays fast."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        trailing_start = await self._history_virtualizer.seed_collapsed_prefix(
            len(messages), DEFAULT_CHUNK_SIZE_MESSAGES)
        tail_widgets = self._render_message_range(messages[trailing_start:], messages)
        tail_widgets.append(
            Static(f"Restored previous session ({len(messages)} messages).", classes="notice"))
        await history.mount(*tail_widgets)
        self._history_virtualizer.force_layout()
        history.scroll_end(animate=False, immediate=True)
        logger.debug("Restored session history: %d messages, %d shown", len(messages), len(tail_widgets))

    def _render_restored_tool_call(
        self, call: ToolCallRequest, response: ChatMessage | None,
    ) -> RenderedToolCall:
        """Reconstruct a finished tool call's `RenderedToolCall` from persisted `Message`s
        for `_mount_restored_history`. A missing `response` renders as a `None` result
        rather than raising."""
        try:
            args = json.loads(call.arguments) if call.arguments else {}
            if not isinstance(args, dict):
                raise ValueError("tool call arguments must decode to a JSON object")
        except (json.JSONDecodeError, ValueError) as json_exc:
            parse_error = f"Invalid JSON in tool call arguments: {json_exc}"
            return self._render_tool_result(call.name, {}, None, parse_error, call.arguments)

        result: Any = None
        error: str | None = None
        if response is not None:
            try:
                parsed = json.loads(response.content)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and "is_error" in parsed:
                if parsed["is_error"]:
                    error = parsed.get("error_message")
                    if error is None:
                        error = json.dumps(parsed.get("response_body"), ensure_ascii=False)
                else:
                    result = parsed.get("response_body")
            elif response.content.startswith("Error: "):
                error = response.content[len("Error: "):]
            else:
                result = parsed if parsed is not None else response.content
        return self._render_tool_result(call.name, args, result, error, None)
