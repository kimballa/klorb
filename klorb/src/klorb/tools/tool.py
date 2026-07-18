# © Copyright 2026 Aaron Kimball
"""Abstract interface for a tool that can be exposed to a model and invoked by name."""

import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    # Deferred to break a cycle: klorb.tools.setup_context -> klorb.process_config ->
    # klorb.session -> klorb.tools.tool (for describe_tool_arg_json_error). ToolSetupContext is
    # only ever used here as a type annotation, never instantiated, so this is safe.
    from klorb.tools.setup_context import ToolSetupContext


def default_tool_call_summary(name: str, args: dict[str, Any], error: str | None) -> str:
    """`Tool.summary()`'s default body, and what a caller renders for a tool call whose name
    isn't recognized by a `ToolRegistry` (so there's no `Tool` instance to call `.summary()`
    on): the tool's name, plus `error` if the call failed.
    """
    return f"{name}: {error}" if error is not None else name


def default_tool_call_detail(
    name: str, args: dict[str, Any], result: Any, error: str | None,
) -> str:
    """`Tool.detail_view()`'s default body, and what a caller renders for a tool call whose
    name isn't recognized by a `ToolRegistry`: pretty-printed JSON of `name` and `args`
    alongside `result` (on success) or `error` (on failure).
    """
    payload: dict[str, Any] = {"name": name, "args": args}
    payload["error" if error is not None else "result"] = error if error is not None else result
    return json.dumps(payload, indent=2, default=str)


def default_invalid_tool_call_summary(name: str, error: str) -> str:
    """What a caller renders for a tool call whose `arguments` string failed to parse as
    JSON (see `klorb.session.ToolCallEvent.raw_arguments`) — there's no parsed `args` dict
    to hand to a `Tool`'s own `summary()`, so this is used unconditionally rather than as a
    per-tool default.
    """
    return f"Invalid tool call generated: {name}: {error}"


def default_invalid_tool_call_detail(name: str, raw_arguments: str, error: str) -> str:
    """`default_invalid_tool_call_summary()`'s detail-view counterpart: the model's raw,
    unparsed `arguments` string alongside the JSON decode error.
    """
    return (
        f"Invalid tool call generated: {name}\n\n"
        f"Arguments (raw, malformed JSON):\n{raw_arguments}\n\n"
        f"Error: {error}"
    )


_EDIT_ARG_NAMES = ("start_text", "end_text", "old_text", "new_text")

_COMMON_JSON_MISTAKES = (
    "Common JSON mistakes:\n"
    "- Unescaped double quotes inside a string (the dominant cause for edit tools): "
    "`\"new_text\": \"print(\"hi\")\"` -> `\"new_text\": \"print(\\\"hi\\\")\"`.\n"
    "- Mismatched/unbalanced brackets or braces: a missing or extra `}`/`]`.\n"
    "- Comma problems: a trailing comma before `}`/`]`, or a missing comma between members: "
    "`{\"a\": 1 \"b\": 2}` -> `{\"a\": 1, \"b\": 2}`.\n"
    "- Mismatched quotes: a string opened with `\"` and never closed, or single quotes used "
    "for a JSON string: `'x'` -> `\"x\"`."
)

_XML_JSON_EXAMPLE = '{ "filename": "foo.py", "start_line": 12, "new_text": "..." }'

_EDIT_ARG_ESCAPE_HINT = (
    "This looks like an edit-tool call (it mentions start_text/end_text/old_text/new_text) "
    "carrying file content -- exactly where embedded double quotes and backslashes break JSON "
    "when they aren't escaped. Double-check the quoting/escaping of those argument values "
    "specifically."
)


def _json_error_fragment(raw_arguments: str, pos: int, window: int = 40) -> str:
    """Quote the raw `arguments` string around byte offset `pos` (±`window` characters) with a
    caret on the line beneath it marking the exact break point, so a model sees the offending
    spot instead of a bare character count it has to count out itself. Embedded newlines within
    the window are preserved in the fragment (so multi-line JSON stays readable) and mirrored
    into the caret line, keeping the caret aligned when the break point is on the fragment's
    last physical line."""
    start = max(0, pos - window)
    end = min(len(raw_arguments), pos + window)
    fragment = raw_arguments[start:end]
    prefix = fragment[:pos - start]
    caret_line = "".join(ch if ch == "\n" else " " for ch in prefix) + "^"
    return f"{fragment}\n{caret_line}"


def describe_tool_arg_json_error(name: str, raw_arguments: str, json_exc: json.JSONDecodeError) -> str:
    """Describe why `raw_arguments` (a tool call's raw, unparsed `arguments` string) failed to
    parse as JSON, for a total tool-call parse failure -- the case where no tool's `apply()`
    ever runs at all. This is the single source both the model-facing `tool_response` error
    (`klorb.session.Session._run_tool_calls`'s `json.JSONDecodeError` branch) and the UI-facing
    `default_invalid_tool_call_detail()` draw from, so the model gets a teaching response
    instead of the bare `str(json_exc)` alone: the precise break-point offset with the
    offending fragment quoted, an XML-vs-JSON detector (a total parse failure is often a model
    emitting markup instead of a JSON object), a fixed primer on the handful of mistakes that
    actually cause these, and -- since edit tools are by far the biggest producers of large,
    heavily-escaped string arguments -- an edit-argument-specific escaping nudge gated on
    whether the raw string even mentions one of those argument names.
    """
    header = f"Invalid JSON in tool call arguments for {name!r}."
    offset = (
        f"Parse error at line {json_exc.lineno}, column {json_exc.colno} "
        f"(char {json_exc.pos}): {json_exc.msg}")
    fragment = _json_error_fragment(raw_arguments, json_exc.pos)

    if raw_arguments.lstrip().startswith("<"):
        xml_body = (
            "The arguments look like XML/markup, not JSON. Tool-call arguments must be a "
            f"single JSON object, e.g.:\n{_XML_JSON_EXAMPLE}")
        return "\n\n".join([header, offset, fragment, xml_body])

    parts = [header, offset, fragment, _COMMON_JSON_MISTAKES]
    if any(arg_name in raw_arguments for arg_name in _EDIT_ARG_NAMES):
        parts.append(_EDIT_ARG_ESCAPE_HINT)
    return "\n\n".join(parts)


def truncate_lines(text: str, max_lines: int) -> str:
    """Return `text` unchanged if it has at most `max_lines` lines, otherwise its first
    `max_lines` lines followed by a trailing `"..."` line — used by a `detail_view()` override
    to cap a long multi-line field (e.g. a file's contents) instead of dumping it in full.
    """
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + "\n..."


class Tool(ABC):
    """Base class for a tool that a model can be offered, and asked to invoke, by name.

    Every concrete `Tool` is constructed with a single `ToolSetupContext` argument, never
    tool-specific constructor arguments, so `ToolRegistry` can instantiate any `Tool`
    subclass uniformly (see `ToolRegistry.instantiate_tool`). A subclass that needs to
    configure itself (e.g. a per-call line limit) pulls the relevant setting out of
    `context` in its own `__init__`.
    """

    def __init__(self, context: "ToolSetupContext") -> None:
        self._context = context

    @property
    def context(self) -> "ToolSetupContext":
        """Return the `ToolSetupContext` this tool was constructed with."""
        return self._context

    @abstractmethod
    def name(self) -> str:
        """Return the tool's name, as reported to the model."""

    @abstractmethod
    def description(self) -> str:
        """Return the tool's description, as reported to the model."""

    @abstractmethod
    def parameters(self) -> dict[str, Any] | type[BaseModel]:
        """Return a JSON schema dict, or a pydantic BaseModel class, describing this tool's arguments."""

    @abstractmethod
    def apply(self, args: dict[str, Any]) -> Any:
        """Execute the tool with the given arguments and return its result."""

    def is_success(self, args: dict[str, Any], result: Any, error: str | None) -> bool:
        """Return whether one call to this tool succeeded, given the parsed `args`, the raw
        `result` returned by `apply()`, and the `error` string (or `None`). The default
        implementation treats `error is None` as success — the tool didn't raise — and
        `error is not None` as failure. Override for a tool whose result shape can indicate
        failure even when no exception was raised (e.g. a tool that returns a status code
        rather than raising on partial failure).

        Called by `Session._run_tool_calls()` to update `SessionStatistics.tools` per-tool
        success/failure counts — see `klorb.session_statistics.ToolCallStats`.
        """
        return error is None

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Return a one-line, human-friendly description of one call to this tool, shown by
        default wherever a UI renders tool call activity (see docs/specs/terminal-repl.md).

        `error is None` means the call succeeded (even if `result` happens to be `None` too);
        `error is not None` means it failed and `result` is meaningless — this is the sole
        success/failure discriminant. Defaults to `default_tool_call_summary()`; override for a
        tool-specific line, e.g. `"Edit file: foo.py (+15/-6)"`.
        """
        return default_tool_call_summary(self.name(), args, error)

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Return a fuller, still human-friendly (not raw JSON, where avoidable) rendering of
        this call's arguments and result/error, shown when a UI's user asks for more detail
        than `summary()` gives.

        Same `error`-is-the-success-discriminant contract as `summary()`. Defaults to
        `default_tool_call_detail()` (pretty-printed JSON); override only when that's a poor
        fit, e.g. to truncate a long field instead of dumping it in full.
        """
        return default_tool_call_detail(self.name(), args, result, error)
