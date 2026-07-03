# © Copyright 2026 Aaron Kimball
"""A Tool that runs `path`/`is_write` through the real permission-evaluation seam
(`evaluate_write`/`resolve_and_evaluate_read` + `raise_if_not_allowed`), used only in tests to
drive `Session._run_tool_calls`' interactive `PermissionAskRequired` handling without needing
real filesystem I/O or a real `ReadFile`/`EditFile` tool call.
"""

from pathlib import Path
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import evaluate_write, resolve_and_evaluate_read
from klorb.tools.tool import Tool


class AskPermissionTool(Tool):
    """Evaluates `path` (`is_write` selects the write or read seam) and either succeeds with a
    `"granted:<path>"` marker, or raises whatever `PermissionError`/`PermissionAskRequired`
    the real permission tables produce for the calling `Tool`'s `ToolSetupContext` — including
    honoring a one-shot `permission_override`, exactly like `EditFile`/`ReadFile` do. Used only
    in tests.
    """

    def name(self) -> str:
        return "ask_permission"

    def description(self) -> str:
        return "Test-only tool: evaluates path/is_write against the real permission tables."

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to evaluate."},
                "is_write": {"type": "boolean", "description": "Evaluate as a write, not a read."},
            },
            "required": ["path"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        path = Path(args["path"])
        is_write = args.get("is_write", False)
        if is_write:
            verdict = evaluate_write(self.context, path)
        else:
            path, verdict = resolve_and_evaluate_read(self.context, str(path))
        raise_if_not_allowed(
            verdict, resource_description=f"access {path}", path=path, is_write=is_write)
        return f"granted:{path}"
