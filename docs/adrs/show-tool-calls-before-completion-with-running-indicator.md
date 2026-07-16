# Show tool calls in history before they finish, with a "Running..." indicator

* Date: 2026-07-09 10:00
* Question: Tool calls currently appear in the history only after they complete (via
  `TurnEventHandlers.on_tool_call`). For long-running tools like Bash, the user sees
  nothing while the command executes. Should we show a "running" indicator immediately
  when a tool call starts, before it finishes?
* Answer: Yes. Add a new `TurnEventHandlers.on_tool_call_started` callback fired from
  `Session._run_tool_calls` just before `tool.apply(args)`, carrying a
  `ToolCallStartedEvent` with `call_id`/`name`/`args`. The TUI mounts a
  `RunningToolCallStatic` widget with a crawling bold-character animation on the word
  "Running..." so the user knows the system hasn't frozen. When the tool completes, the
  existing `on_tool_call` callback finalizes the running widget in place (via
  `finalize()`) rather than mounting a duplicate.
* Reasoning: The "started" callback fires after args are parsed and the tool is resolved,
  but before `tool.apply(args)` — this is the earliest point where we know the tool will
  actually execute. If a permission check inside `apply()` denies the call, the subsequent
  `on_tool_call` error callback replaces the running indicator with the error, which is
  acceptable (a brief "Running..." flash for denied calls). The running indicator uses a
  crawl animation (one bold character cycling through "Running..." at 120ms per frame) to
  distinguish a live system from a frozen one. `RunningToolCallStatic` inherits from
  `ToolCallStatic` so existing `history.query(ToolCallStatic)` searches and the Ctrl+O
  detail toggle work unchanged. The animation uses `Rich.text.Text` spans for
  per-character styling rather than console markup, since the tool label text is arbitrary
  and could contain characters that clash with markup syntax.
