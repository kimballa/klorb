# Diff/read previews widen ToolCallStatic's content type, rather than adding parallel widget classes

* Date: 2026-07-19 20:00
* Question: A diff preview needs colored, line-numbered content and a click-to-expand overlay;
  today's `ToolCallStatic`/`RunningToolCallStatic` only ever hold a plain `str` summary/detail
  pair. Should diff/read previews get their own widget classes (e.g. `DiffPreviewStatic`,
  `ReadPreviewStatic`) mounted in place of `ToolCallStatic` for those calls, or should
  `ToolCallStatic` itself be widened to accept richer content and an optional click callback?
* Answer: Widen `ToolCallStatic`. Its `summary_content`/`detail_content` constructor (and
  `RunningToolCallStatic.finalize()`) parameters are now typed `textual.visual.VisualType`
  (accepting a plain `str`, exactly as before, or a pre-built `textual.content.Content`) instead
  of `str`, plus an optional `on_click: Callable[[], None] | None`. No new widget class was added.
* Reasoning: `Ctrl+O` (`action_toggle_tool_call_detail`) already globally toggles every
  `ToolCallStatic` in the history between its summary and detail content by calling
  `set_detail_shown()` in a loop over `self._tool_call_widgets` — a list typed `list[ToolCallStatic]`.
  A parallel widget class for diff/read previews would need that loop (and every other
  `ToolCallStatic`-typed piece of code: `_tool_call_widgets`, `_running_tool_call_widgets`,
  `history.query(ToolCallStatic)` lookups) to either special-case the new class or be widened to a
  shared protocol/base — real churn for a UI-only distinction, since a diff preview's *content* is
  the only thing that differs, not its role in the detail toggle or the running→finalized state
  machine. `RunningToolCallStatic` in particular inherits from `ToolCallStatic` and transitions a
  single mounted widget instance from its running animation to final content in place
  (`finalize()`) — swapping to a different Python class mid-lifecycle for some calls but not
  others would have meant either duplicating that whole animate/finalize machinery in a second
  class or contorting `finalize()` into a factory that remounts a different widget, neither of
  which is simpler than accepting a richer content type. `Static.update()`/`Static.__init__()`
  already accept any `VisualType` (a `Content` renders through the same widget as a plain `str`),
  so nothing about `Static`'s own rendering needed to change — only the type this code declares it
  passes through.
