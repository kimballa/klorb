# Tool framework

## Summary

A `Tool` is a unit of functionality a model can be offered, and asked to invoke, while
answering a prompt (a.k.a. "function calling"). `ToolRegistry` discovers `Tool`
implementations and acts as a factory for them, building the tool definitions sent to the
model alongside a prompt and instantiating a fresh `Tool` per call. This is a framework-level
feature: individual tools (file search, shell exec, etc.) will be added under
`klorb/src/klorb/tools/` as separate modules later and picked up automatically. See
[[session-and-turns]] for how `Session` actually wires a `ToolRegistry` into the turn loop.

## How it works

* `klorb.tools.setup_context.ToolSetupContext` (`klorb/src/klorb/tools/setup_context.py`) is
  a pydantic `BaseModel` holding `process_config: ProcessConfig` and
  `session_config: SessionConfig` — references to the actual config objects, not individual
  settings pre-extracted from them. `session_config` is the *live* `Session.config`, not
  `process_config.session` (only the template a session's config is copied from — see
  [[process-and-session-config]]). See
  [the ToolSetupContext ADR](../adrs/tool-setup-context-carries-process-and-session-config.md)
  for why it holds the config objects themselves rather than flattened fields.
* `klorb.tools.tool.Tool` (`klorb/src/klorb/tools/tool.py`) is an abstract base class. Its
  `__init__(self, context: ToolSetupContext)` is concrete (not abstract) and imposes a
  standard constructor on every subclass: a `Tool` is always constructed with exactly one
  `ToolSetupContext` argument, never tool-specific constructor arguments, so `ToolRegistry`
  can instantiate any `Tool` subclass uniformly. A subclass that needs to configure itself
  (e.g. a per-call line limit) pulls the relevant setting out of `context` in its own
  `__init__`, after calling `super().__init__(context)`. The stored context is available to
  subclasses via the `context` property. Concrete tools implement:
  * `name() -> str` — the tool's name, as reported to the model.
  * `description() -> str` — the tool's description, as reported to the model.
  * `parameters() -> dict[str, Any] | type[BaseModel]` — the tool's argument schema, either
    a raw JSON schema dict or a pydantic `BaseModel` subclass.
  * `apply(args: dict[str, Any]) -> Any` — runs the tool given a dict of arguments (as
    returned by the model) and returns the result.
* `klorb.tools.registry.ToolRegistry` (`klorb/src/klorb/tools/registry.py`) is constructed
  with `(process_config: ProcessConfig, session_config: SessionConfig, package: ModuleType =
  klorb.tools)` — held by reference, not copied, so later changes to either (e.g. a TUI
  command palette mutating `session_config` in place) are picked up by tools instantiated
  afterward. It discovers `Tool` subclasses by walking `package`'s modules with
  `pkgutil.iter_modules`, importing each, and collecting concrete (non-abstract) `Tool`
  subclasses defined directly in that module — exactly once, in `__init__`; it never
  re-scans. By default it scans the `klorb.tools` package itself, so dropping a new module
  containing a `Tool` subclass into `klorb/src/klorb/tools/` is enough to register it — no
  manual registration step is required. A different package can be passed to the
  constructor (used by tests to scan a fixture package instead).
  * `instantiate_tool(name: str) -> Tool` — the factory method: builds a fresh
    `ToolSetupContext` from the registry's current `process_config`/`session_config` and
    constructs a brand new instance of the named tool's class, raising `KeyError` if no tool
    with that name was discovered. Called once per requested tool call by
    `Session._run_tool_calls` (see [[session-and-turns]]), so a tool never carries state over
    between calls. See
    [the fresh-instance-per-call ADR](../adrs/tool-registry-instantiates-a-fresh-tool-per-call.md).
  * `tools() -> list[Tool]` — a freshly-instantiated `Tool` for every discovered tool.
  * `tool_definitions() -> list[dict[str, Any]]` — builds the OpenAI/OpenRouter
    function-calling `tools` array: each entry is
    `{"type": "function", "function": {"name", "description", "parameters"}}`, with
    pydantic parameter schemas converted to JSON schema via `model_json_schema()`.

## Built-in tools

* `klorb.tools.read_file.ReadFileTool` (`klorb/src/klorb/tools/read_file.py`), name
  `ReadFile`. Reads a text file given a mandatory `filename`, and optional 1-indexed
  `start_line`/`end_line` (inclusive). `start_line` of `0` or omitted means start at the
  beginning of the file; `end_line` omitted means read up to the per-call line cap from
  `start_line`. At most `context.process_config.read_file_max_lines` lines (default
  `MAX_LINES`, 200) are returned per call regardless of the requested range, so an agent
  pages through larger files with successive calls. The result is a dict: `filename`, the
  actual `start_line`/`end_line` returned, the file's `total_lines`, a `truncated` flag (true
  when more content exists past `end_line`), and `content` — a single string with one
  `"N|line text"` entry per line, newline-separated.

## Out of scope

* Recursive discovery into subpackages of `klorb.tools` is not implemented; tools are
  expected to live as flat modules directly under `klorb/src/klorb/tools/`.
