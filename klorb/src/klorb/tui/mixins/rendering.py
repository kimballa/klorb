# © Copyright 2026 Aaron Kimball
"""RenderingMixin: mounting and updating response/thinking/tool-call widgets in the
history for ReplApp."""

import json
from typing import Any

from textual.containers import VerticalScroll
from textual.content import Content
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
    prefix_with_header,
    render_diff_content,
    render_full_file_content,
    render_read_preview_content,
    strip_system_interjections,
    summarize_reasoning_details,
)
from klorb.tui.panels.preview_screens import DiffDetailScreen, ReadDetailScreen
from klorb.tui.widgets.tool_call_widgets import (
    GettingReadyStatic,
    RenderedToolCall,
    RunningToolCallStatic,
    ToolCallStatic,
    TurnWaitingStatic,
)

THINKING_LABEL = "<Thinking>"
REASONING_DETAILS_LABEL = "<Reasoning>"
TOOL_USE_LABEL = "<Tool use>"

_DIFF_PREVIEW_MAX_LINES = 8
"""How many diff lines `RenderedToolCall.summary_content` shows inline for an edit/create call
before truncating with a trailing `"..."` -- see `render_diff_content`. Ctrl+O's detail view and
the click-to-expand overlay both show the full (untruncated) diff instead."""


class RenderingMixin(ReplAppBase):
    """Response, thinking, and tool-call rendering/mounting into the history scroll --
    see `ReplApp` for how this mixes into the concrete app class."""

    def _mount_response_widget(self, initial_text: str) -> Markdown:
        """Mount a new `Markdown` widget for a streaming response and return it. Also refreshes
        the status bar's token tally (see `_update_status_bar`): a new placeholder `Message`
        with its own `num_tokens` was just appended to the session for this chunk (see
        `Session._send_and_receive.handle_chunk`), so the footer should reflect it immediately
        rather than only once the whole turn finishes. Clears the turn's `TurnWaitingStatic`
        first (see `_clear_turn_waiting_widget`): visible response text is real content, so the
        "still working" placeholder has nothing left to stand in for.
        """
        self._clear_turn_waiting_widget()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = Markdown(initial_text, classes="response")
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        return widget

    def _update_response_widget(self, widget: Markdown, text: str) -> None:
        """Update a streaming response `Markdown` widget with the latest accumulated `text`,
        following the view to the bottom only if it was already pinned there before this change
        (see `_history_pinned_to_bottom`), so a user who's scrolled up to reread earlier output
        isn't yanked back down by every incoming chunk. `widget` is always still the last thing
        mounted in the history at this point: `_send_prompt` starts a fresh widget rather than
        reusing this one once a round boundary (a tool call) has passed, so there's nothing
        mounted after it left to get stuck behind. Also refreshes the status bar (see
        `_mount_response_widget`): the placeholder message's `num_tokens` grew along with
        `text`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()

    def _mount_thinking_widget(self, initial_text: str) -> tuple[Static, Static]:
        """Mount a left-justified `<Thinking>` label followed by an italicized `Static`
        widget for a streaming thinking block, and return `(body_widget, label_widget)`. The
        body is styled italic via the `.thinking-body` CSS class rather than markup, and
        constructed with `markup=False`: model thinking text is arbitrary and must render
        verbatim, not be parsed as Textual console markup (an unescaped `[` in it can
        otherwise be misread as the start of a markup tag and raise `MarkupError`). Also
        refreshes the status bar (see `_mount_response_widget`): a new `role="thinking"`
        placeholder message with its own `num_tokens` was just appended to the session. Clears
        the turn's `TurnWaitingStatic` first, same as `_mount_response_widget`.
        """
        self._clear_turn_waiting_widget()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(THINKING_LABEL, classes="thinking-label")
        history.mount(label_widget)
        widget = Static(initial_text, classes="thinking-body", markup=False)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        return widget, label_widget

    def _update_thinking_widget(self, widget: Static, text: str) -> None:
        """Update a streaming `<Thinking>` `Static` widget with the latest accumulated `text`,
        following the view to the bottom only if it was already pinned there before this
        change (see `_update_response_widget`) — the label mounted alongside `widget` never
        changes after being set, so only the body needs updating here. Also refreshes the
        status bar: the placeholder message's `num_tokens` grew along with `text`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()

    def _mount_reasoning_details_widget(self, text: str) -> tuple[Static, Static]:
        """Mount a left-justified `<Reasoning>` label followed by an italicized `Static`
        widget showing `text` (see `summarize_reasoning_details`), mirroring
        `_mount_thinking_widget`'s label/body split and `markup=False` rationale, and return
        `(body_widget, label_widget)`. Also refreshes the status bar: a new `role="thinking"`
        placeholder message's `reasoning_details` was just set on the session (see
        `Session._send_and_receive.handle_reasoning_details_chunk`).
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(REASONING_DETAILS_LABEL, classes="reasoning-details-label")
        history.mount(label_widget)
        widget = Static(text, classes="reasoning-details-body", markup=False)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()
        return widget, label_widget

    def _update_reasoning_details_widget(self, widget: Static, text: str) -> None:
        """Update a `<Reasoning>` `Static` widget with the latest `text` (see
        `summarize_reasoning_details`), mirroring `_update_thinking_widget`."""
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget.update(text)
        self._scroll_if_pinned(history, was_pinned)
        self._update_status_bar()

    def _render_tool_call(self, event: ToolCallEvent) -> RenderedToolCall:
        """Render `event` as a `RenderedToolCall` — see `_render_tool_result`, which does the
        actual work from `event`'s individual fields. Split out so `_render_restored_tool_call`
        (reconstructing a finished call from persisted `Message`s rather than a live
        `ToolCallEvent`) can share the same rendering logic.
        """
        return self._render_tool_result(
            event.name, event.args, event.result, event.error, event.raw_arguments)

    def _render_tool_result(
        self, name: str, args: dict[str, Any], result: Any, error: str | None,
        raw_arguments: str | None,
    ) -> RenderedToolCall:
        """Render one finished tool call as a `RenderedToolCall` — instantiating a fresh `Tool`
        purely to call its pure `summary()`/`detail_view()`/`diff_preview()`/`read_preview()`
        methods (`ToolRegistry.instantiate_tool()` is already a cheap, no-shared-state
        operation; see docs/adrs/tool-registry-instantiates-a-fresh-tool-per-call.md) — or via
        the shared default formatters if `name` isn't a registered tool (e.g. a hallucinated
        tool name, which `Session._run_tool_calls` already turned into `error`).

        `raw_arguments is not None` means the model's `arguments` string failed to parse as
        JSON before any tool ever ran (see `ToolCallEvent.raw_arguments`); that's rendered via
        `default_invalid_tool_call_summary()`/`default_invalid_tool_call_detail()` instead,
        showing the raw malformed text rather than the always-empty `args`.

        A tool whose `diff_preview()`/`read_preview()` returns non-`None` (only the `EditFile`/
        `CreateFile`/`Read*` families and their memory/scratchpad/skill counterparts) gets a
        colored diff or numbered-content `Content` instead of `summary()`/`detail_view()`'s
        plain text, plus an `on_click` that pushes the matching overlay
        (`klorb.tui.panels.preview_screens`) -- see those two methods' docstrings on `Tool` for
        the full contract. Every other tool renders exactly as before.
        """
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
        """Push `DiffDetailScreen` showing `content` (the same already-built full-diff `Content`
        Ctrl+O's detail view shows) -- factored out of `_render_tool_result` purely so the
        `on_click` closure it builds has a plain `() -> None` callable to hold, matching
        `RenderedToolCall.on_click`'s type (`Screen.push_screen()` itself returns an awaitable,
        not `None`)."""
        self.push_screen(DiffDetailScreen(label, content))

    def _open_read_detail_screen(self, preview: ReadPreview) -> None:
        """Perform `preview`'s lazy full-subject read only now, at click time (see
        `klorb.tools.tool.ReadPreview.open_full`), and push `ReadDetailScreen` with the result --
        an in-overlay message instead if the read failed (e.g. the subject was deleted, moved, or
        became unreadable since it was originally read)."""
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

    def _mount_tool_call_widget(self, rendered: RenderedToolCall) -> tuple[ToolCallStatic, Static]:
        """Mount a left-justified `<Tool use>` label (styled via the `.tool-call-label` CSS
        class, matching `<Thinking>`'s label/body split in `_mount_thinking_widget`) followed
        by a `ToolCallStatic` for one finished tool call, and return `(widget, label_widget)`.
        Applies the current global detail-shown state (see `action_toggle_tool_call_detail`)
        so a call rendered while detail view is already active shows detail immediately rather
        than a summary the user would have to toggle past. Also refreshes the status bar (see
        `_mount_response_widget`): `Session._run_tool_calls` has already appended this call's
        `role="tool_response"` message (with its own `num_tokens`) by the time
        `callbacks.on_tool_call` — and so this method — runs.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(TOOL_USE_LABEL, classes="tool-call-label")
        history.mount(label_widget)
        widget = ToolCallStatic(rendered.summary_content, rendered.detail_content, rendered.on_click)
        widget.set_detail_shown(self._tool_call_detail_shown)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._tool_call_widgets.append(widget)
        self._update_status_bar()
        return widget, label_widget

    def _render_tool_call_summary(self, name: str, args: dict[str, Any]) -> str:
        """Render a tool call's pre-execution summary (the label shown in the running
        indicator) by calling `Tool.summary(args)` with no `result` or `error`. Every
        tool's `summary()` produces a meaningful label when called this way -- e.g.
        ``Bash: <intent>\\n$ <command>`` for BashTool -- since the result/error suffix is
        only appended when those parameters are non-`None`. Falls back to the module-level
        `default_tool_call_summary()` if the tool name isn't registered.
        """
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
        """Mount a ``<Tool use>`` label followed by a `RunningToolCallStatic` showing the
        tool's pre-execution label with a crawling ``Running...`` animation. Stores the
        widget in `_running_tool_call_widgets` keyed by `call_id` so the eventual
        `handle_tool_call` completion callback can finalize it in place rather than mounting
        a duplicate. Also tracks it in `_tool_call_widgets` for the Ctrl+O detail toggle. Clears
        the turn's `TurnWaitingStatic` first, same as `_mount_response_widget`: this only mounts
        once a call is far enough along to actually start running, not while its arguments are
        still streaming in, so it's a meaningful "no longer just waiting" signal.
        """
        self._clear_turn_waiting_widget()
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        label_widget = Static(TOOL_USE_LABEL, classes="tool-call-label")
        history.mount(label_widget)
        widget = RunningToolCallStatic(summary_text)
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        self._tool_call_widgets.append(widget)
        self._running_tool_call_widgets[call_id] = widget
        self._update_status_bar()
        return widget

    def _mount_getting_ready_widget(self) -> GettingReadyStatic:
        """Mount a `GettingReadyStatic` into history, showing an animated "Getting ready..."
        notice while `PromptSubmissionMixin._run_session_naming`'s classifier call is in
        flight -- the naming-step analog of `_mount_running_tool_call_widget`, but with no
        `_running_tool_call_widgets`/`_tool_call_widgets` tracking, since this widget is
        always removed outright (`GettingReadyStatic.remove_self`) rather than finalized in
        place once naming resolves.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = GettingReadyStatic()
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _mount_turn_waiting_widget(self) -> TurnWaitingStatic:
        """Mount a `TurnWaitingStatic` into history, showing an animated "still working" notice
        -- the per-turn analog of `_mount_getting_ready_widget`, but for every turn rather than
        only the first, and cleared by `_clear_turn_waiting_widget` rather than removed directly
        by its caller. Called by `_send_prompt` once it's actually about to start the turn (after
        `_run_session_naming`, if that ran for this turn), not by `_submit_prompt` at echo time,
        so it never overlaps the first turn's own `GettingReadyStatic`.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        was_pinned = self._history_pinned_to_bottom
        widget = TurnWaitingStatic()
        history.mount(widget)
        self._scroll_if_pinned(history, was_pinned)
        return widget

    def _clear_turn_waiting_widget(self) -> None:
        """Remove `self._turn_waiting_widget` (if it's still mounted) and stop its timer. Called
        from the first of `_mount_response_widget`/`_mount_thinking_widget`/
        `_mount_running_tool_call_widget` to fire for the in-flight turn, and again
        (idempotently -- a no-op once `_turn_waiting_widget` is already `None`) from
        `_finish_turn` as a fallback for a turn that ends without ever reaching any of those
        (e.g. an error before any content streamed).
        """
        if self._turn_waiting_widget is None:
            return
        widget = self._turn_waiting_widget
        self._turn_waiting_widget = None
        widget.remove_self()

    def _running_tool_call_anchor(self) -> Static | None:
        """The `<Tool use>` label widget mounted just above the tool call currently running,
        if any — so an interaction record (a permission ask / question / escalation raised from
        inside that call's `apply()`) can be floated above the whole `<Tool use>` block that
        triggered it, rather than appended below its `Running…` indicator. Tool calls run
        serially, so at most one running-tool widget is un-finalized at a time; this returns
        that widget's immediately-preceding `.tool-call-label` sibling (or the running widget
        itself, defensively, if no such label precedes it), and `None` when nothing is running.
        """
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
        summary/detail content. Called from `handle_tool_call` via `call_from_thread` when a
        tool call completes -- the widget was mounted earlier by
        `_mount_running_tool_call_widget` and is already in the history scroll and the
        Ctrl+O tracking list."""
        widget.finalize(rendered.summary_content, rendered.detail_content, rendered.on_click)
        widget.set_detail_shown(self._tool_call_detail_shown)
        self._update_status_bar()

    def _mount_restored_history(self, messages: list[ChatMessage]) -> None:
        """Re-render `messages` — a previous session's history, already loaded onto
        `self._session` by `_maybe_restore_last_session` — into the history scroll, so a
        restored conversation reads the same way it would have live: one `Static`/`Markdown`/
        `ToolCallStatic` per user/assistant/thinking/tool-use message, in original order, via
        the same `_mount_response_widget`/`_mount_thinking_widget`/`_mount_tool_call_widget`
        helpers a live turn uses. A `"thinking"` message's `reasoning_details`, if it carries
        one, is rendered right after it via `_mount_reasoning_details_widget`, subject to the
        same `summarize_reasoning_details()` suppression a live turn applies.
        `role="system"`/`"tool_defs"` bookkeeping messages are skipped, matching how they're
        never rendered live either (see `Session._ensure_system_message`/
        `_ensure_tool_defs_message`). A `role="tool_response"` message is rendered together
        with its matching `role="tool_use"` entry, via `_render_restored_tool_call`, rather
        than on its own.
        """
        history = self.query_one(f"#{HISTORY_ID}", VerticalScroll)
        responses_by_call_id = {
            message.tool_call_id: message for message in messages
            if message.role == "tool_response" and message.tool_call_id is not None
        }
        for message in messages:
            if message.role == "user":
                display_content = strip_system_interjections(message.content)
                history.mount(Static(display_content, classes="prompt", markup=False))
            elif message.role == "assistant":
                text = message.content
                if message.processing_state == "aborted":
                    text = f"{text}\n\n*(interrupted)*"
                self._mount_response_widget(text)
            elif message.role == "thinking":
                text = message.content
                if message.processing_state == "aborted":
                    text = f"{text}\n\n(interrupted)"
                self._mount_thinking_widget(text)
                if message.reasoning_details:
                    reasoning_details_text = summarize_reasoning_details(message.reasoning_details)
                    if reasoning_details_text is not None:
                        self._mount_reasoning_details_widget(reasoning_details_text)
            elif message.role == "tool_use":
                for call in message.tool_calls or []:
                    rendered = self._render_restored_tool_call(
                        call, responses_by_call_id.get(call.id))
                    self._mount_tool_call_widget(rendered)
        history.mount(Static(f"Restored previous session ({len(messages)} messages).", classes="notice"))
        history.scroll_end(animate=False)

    def _render_restored_tool_call(
        self, call: ToolCallRequest, response: ChatMessage | None,
    ) -> RenderedToolCall:
        """Reconstruct a finished tool call's `RenderedToolCall` for `_mount_restored_history`
        from persisted `Message`s alone — `call.arguments` (the model's raw JSON-encoded
        arguments) and `response.content`, a JSON-serialized `klorb.tools.response_envelope.
        ToolResponseEnvelope` (see `Session._run_tool_calls`), the only form either survives in
        once persisted. A `response.content` predating this envelope format (a session saved by
        an older klorb version) falls back to the old `"Error: {message}"`-or-bare-string/JSON
        encoding, best-effort: a successful string result that happens to start with
        `"Error: "` is indistinguishable there from an actual failure, since the two were folded
        into the same `content` string on write; every other pre-envelope shape round-trips
        exactly. A missing `response` (a truncated or hand-edited save file) renders as a call
        with a `None` result rather than raising.
        """
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
                        error = json.dumps(parsed.get("response_body"))
                else:
                    result = parsed.get("response_body")
            elif response.content.startswith("Error: "):
                error = response.content[len("Error: "):]
            else:
                result = parsed if parsed is not None else response.content
        return self._render_tool_result(call.name, args, result, error, None)
