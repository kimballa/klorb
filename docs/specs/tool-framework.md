# Tool framework

## Summary

A `Tool` is a unit of functionality a model can be offered, and asked to invoke, while
answering a prompt (a.k.a. "function calling"). `ToolRegistry` discovers `Tool`
implementations and builds the tool definitions sent to the model alongside a prompt. This
is a framework-level feature: individual tools (file search, shell exec, etc.) will be
added under `klorb/src/klorb/tools/` as separate modules later and picked up automatically.

## How it works

* `klorb.tools.tool.Tool` (`klorb/src/klorb/tools/tool.py`) is an abstract base class.
  Concrete tools implement:
  * `name() -> str` — the tool's name, as reported to the model.
  * `description() -> str` — the tool's description, as reported to the model.
  * `parameters() -> dict[str, Any] | type[BaseModel]` — the tool's argument schema, either
    a raw JSON schema dict or a pydantic `BaseModel` subclass.
  * `apply(args: dict[str, Any]) -> Any` — runs the tool given a dict of arguments (as
    returned by the model) and returns the result.
* `klorb.tools.registry.ToolRegistry` (`klorb/src/klorb/tools/registry.py`) discovers
  `Tool` subclasses by walking a package's modules with `pkgutil.iter_modules`, importing
  each, and collecting concrete (non-abstract) `Tool` subclasses defined directly in that
  module. By default it scans the `klorb.tools` package itself, so dropping a new module
  containing a `Tool` subclass into `klorb/src/klorb/tools/` is enough to register it — no
  manual registration step is required. A different package can be passed to the
  constructor (used by tests to scan a fixture package instead).
  * `tools() -> list[Tool]` — all discovered tools.
  * `get(name: str) -> Tool` — look up a discovered tool by name.
  * `tool_definitions() -> list[dict[str, Any]]` — builds the OpenAI/OpenRouter
    function-calling `tools` array: each entry is
    `{"type": "function", "function": {"name", "description", "parameters"}}`, with
    pydantic parameter schemas converted to JSON schema via `model_json_schema()`.

## Built-in tools

* `klorb.tools.read_file.ReadFileTool` (`klorb/src/klorb/tools/read_file.py`), name
  `ReadFile`. Reads a text file given a mandatory `filename`, and optional 1-indexed
  `start_line`/`end_line` (inclusive). `start_line` of `0` or omitted means start at the
  beginning of the file; `end_line` omitted means read up to the per-call line cap from
  `start_line`. At most `MAX_LINES` (200) lines are returned per call regardless of the
  requested range, so an agent pages through larger files with successive calls. The
  result is a dict: `filename`, the actual `start_line`/`end_line` returned, the file's
  `total_lines`, a `truncated` flag (true when more content exists past `end_line`), and
  `content` — a single string with one `"N|line text"` entry per line, newline-separated.

## Out of scope

* Wiring `ToolRegistry.tool_definitions()` into the
  [OpenRouter prompt client](openrouter-prompt-client.md)'s request, and dispatching model
  tool-call requests to `Tool.apply()`, is future work — this spec covers the framework
  and built-in tools, not the agent loop that uses them.
* Recursive discovery into subpackages of `klorb.tools` is not implemented; tools are
  expected to live as flat modules directly under `klorb/src/klorb/tools/`.
