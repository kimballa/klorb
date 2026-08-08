# Session.TurnEventHandlers.on_tool_call carries raw call data, not pre-rendered display strings

* Date: 2026-07-03 18:13
* Question: The REPL needs to show each tool call as it happens (see
  [[terminal-repl]]/[[tool-framework]]), via a new `TurnEventHandlers.on_tool_call` callback
  fired from `Session._run_tool_calls` once per finished call. Should the `ToolCallEvent` it's
  called with carry pre-rendered display strings (a one-line summary and a fuller detail view,
  computed inside `Session`), or raw structured data (`name`/`args`/`result`/`error`) for the
  consumer to render itself via `Tool.summary()`/`Tool.detail_view()`?
* Answer: Raw data. `ToolCallEvent` carries `call_id`, `name`, `args`, `result`, and `error`
  only. A consumer (`klorb.tui.repl.ReplApp._render_tool_call`) renders it by calling
  `ToolRegistry.instantiate_tool(name)` a second time — purely to invoke that instance's pure
  `summary()`/`detail_view()` methods — falling back to the module-level
  `default_tool_call_summary()`/`default_tool_call_detail()` formatters if `name` isn't a
  registered tool.
* Reasoning: `klorb.tools.tool` already transitively depends on `klorb.session` — `tool.py`
  imports `klorb.tools.setup_context`, which imports `from klorb.session import SessionConfig`
  — so `session.py` cannot import `klorb.tools.tool.Tool` at runtime to call `.summary()`/
  `.detail_view()` itself without introducing a circular import; it already routes around the
  same constraint for `ToolRegistry` via a `TYPE_CHECKING`-only import (see
  [the ToolSetupContext ADR](tool-setup-context-carries-process-and-session-config.md)).
  Pre-rendering inside `Session` is therefore not available as an option, not merely a less
  tidy one. Raw data also keeps `Session` ignorant of whether or how a call is displayed: a
  headless one-shot run that never registers `on_tool_call` pays no formatting cost at all,
  and a future consumer (e.g. the VSCode plugin) can render the same event differently without
  `Session` knowing anything changed. Instantiating a tool a second time purely to call a pure
  method on it reuses the same "cheap, no shared state" pattern
  [[tool-registry-instantiates-a-fresh-tool-per-call]] already establishes for dispatching a
  call in the first place.
