# ToolRegistry is a session-scoped factory that builds a fresh Tool instance per call, not a cache of singletons

* Date: 2026-07-01 16:45
* Question: `ToolRegistry` (`klorb/src/klorb/tools/registry.py`) previously discovered each
  `Tool` subclass once and kept one long-lived instance per name, handed out by `get(name)`.
  Now that a `Tool` is constructed from a `ToolSetupContext` wrapping the *live*
  `ProcessConfig`/`SessionConfig` (see
  [the ToolSetupContext ADR](tool-setup-context-carries-process-and-session-config.md)), and
  `Session` will dispatch a model's tool-call requests through the same registry across many
  turns, should `ToolRegistry` keep handing out those same cached instances, or build a new
  one on every call? And how long should a `ToolRegistry` itself live — rebuilt per turn, or
  once per `Session`?
* Answer: `ToolRegistry` is constructed once per `Session` (`process_config: ProcessConfig`,
  `session_config: SessionConfig` passed into its constructor and held by reference) and
  scans `klorb.tools` for concrete `Tool` subclasses exactly once, in `__init__` — it never
  re-scans. It stores the discovered *classes*, not instances. `instantiate_tool(name) ->
  Tool` is the factory method: it builds a fresh `ToolSetupContext` from the registry's
  current `process_config`/`session_config` and constructs a brand new instance of that
  tool's class every time it's called (also used internally by `tools()` and
  `tool_definitions()`). `Session._run_tool_calls` calls `instantiate_tool()` once per
  requested tool call, every round.
* Reasoning: A tool never carries state between calls this way, which matters once a `Tool`
  can be invoked many times across a multi-round tool-calling turn (see
  [[wire-tool-calling-into-the-session-turn-loop]]) — there's no risk of a stale `self._max_lines`
  or similar surviving from an earlier call after a config change mid-session. Discovery
  (`pkgutil.iter_modules`) is the only genuinely expensive part of building a `ToolRegistry`,
  and it doesn't depend on config at all, so running it once per `Session` (not once per call,
  not once per turn) avoids repeated filesystem/import work for something that never changes
  after startup. `/clear` (`ReplApp.clear_session`) still constructs a brand new `ToolRegistry`
  for the brand new `Session` it creates, exactly as it already does for `Session` itself —
  the registry's lifetime is tied to the session it belongs to, not reused across `/clear`.
