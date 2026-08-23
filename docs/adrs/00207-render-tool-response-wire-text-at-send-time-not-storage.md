# Tool response wire text is rendered per-tool at send time, not stored

* Date: 2026-08-23 00:00
* Question: A `tool_response` `Message.content` was one JSON document
  (`ToolResponseEnvelope.to_wire_dict()`), so `response_body` — whatever `Tool.apply()` returned —
  reached the model as a nested JSON value. For the `ReadFileCore`-based read tools, that means a
  file's content arrives escaped inside a JSON string with no natural header/content separation.
  Should each tool control how its own result renders to the model, and if so, where does that
  rendering happen relative to storage and to `system_interjections` (until now a JSON field on
  the same envelope, not XML, unlike the `<SystemInterjection>` mechanism `role="user"` turns
  already use)?
* Answer: `Tool.format_response(apply_output: Any) -> str` (`klorb.tools.tool`) renders a tool's
  own result; the default is `json.dumps(apply_output, ensure_ascii=False)`, and the four
  `ReadFileCore`-based tools override it via `format_read_result()`
  (`klorb.tools.util.read_file_core`) to emit `key: value` header lines (`start_line`,
  `truncated`, an optional `namespace`/`filename`, etc.) followed by a blank line and the file
  content.

  `ToolResponseEnvelope.to_wire_content(tool: Tool | None) -> str` composes the full text a model
  sees for one call: each `system_interjections` entry as `<SystemInterjection subject="...">`
  XML (`wrap_system_interjection()`, moved into `klorb.tools.response_envelope` from
  `klorb.session.mixins.turns` since both the user-prompt and tool-response paths need it now),
  then `is_error`/`error_category`/`is_retryable`/`error_message` as header lines — emitted only
  when `is_error` is true, never as a `false`/absent-value line — then `response_body` rendered
  via `tool.format_response()` (a plain JSON dump when `tool` is `None`, e.g. an unresolved tool
  name).

  Crucially, this rendering is computed lazily, at the point a request is actually sent, not at
  tool-execution time. `SessionToolExecutionMixin._run_tool_calls` and `ToolResponseEnvelope.
  to_wire_dict()` are unchanged: `Message.content` for a `tool_response` is still persisted as the
  structured envelope JSON it always was. `SessionTurnsMixin._build_wire_message_snapshot()`,
  called once inside `_send_and_receive()`, rebuilds a transient copy of the outbound message list
  for each request: `ToolResponseEnvelope.model_validate_json(message.content)` recovers the
  stored envelope, the originating tool is resolved by correlating `tool_call_id` back to the
  preceding `tool_use` message's matching call name (the same technique the TUI's restore
  rendering and the ACP server's replay builder already use for their own purposes), and
  `to_wire_content(tool)` produces the text actually sent. `self._messages` itself is never
  mutated.
* Reasoning: Keeping storage as the structured envelope means restore/replay (`klorb.tui.mixins.
  rendering._render_restored_tool_call`, `klorb.server.update_mapping._replay_tool_call_entry`)
  and every `Tool.summary()`/`detail_view()`/`read_preview()` override need no changes at all —
  they already read the `apply()`-shaped dict directly, live or restored, and continue to. The
  alternative (formatting once at tool-execution time and storing the formatted text) would need a
  reverse parser to recover structure for those two consumers, which is exactly the kind of
  format-specific round-trip that breaks quietly the day a tool's `format_response()` changes
  shape. Deriving the wire text fresh from the same stored JSON on every send is a pure function
  of `(envelope, tool)`, so a resumed session sends identically to a live one with no special
  casing.

  `is_error`/`is_retryable` are omitted entirely on a successful call rather than spelled out as
  `false`, since a header block that's only ever meaningful on failure would otherwise cost every
  successful tool call — the overwhelming majority — tokens for no signal.

  Surfacing `system_interjections` in the TUI/vscode-plugin history view (it reaches no UI today,
  live or replayed — `ToolCallEvent` carries no such field) is out of scope here; see `TODO.md`.
