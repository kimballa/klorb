# © Copyright 2026 Aaron Kimball
"""A Tool that runs several `path`s through the real permission-evaluation seam and raises
`MultiPermissionAskRequired` (rather than a single `PermissionAskRequired`) when more than one
needs confirmation, used only in tests to drive `Session._run_tool_calls`'/`_resolve_multi_
permission_ask`'s serial multi-item ask handling without needing a real `BashTool` invocation.
"""

from pathlib import Path
from typing import Any

from klorb.permissions.resource import PathResource
from klorb.permissions.table import MultiPermissionAskRequired, PermissionAskItem
from klorb.permissions.workspace import evaluate_write
from klorb.tools.tool import Tool


class AskMultiPermissionTool(Tool):
    """Evaluates every path in `paths` (always as a write) against the real `writeDirs`/
    `readDirs` tables. Any `"deny"` anywhere short-circuits to an immediate `PermissionError`
    (no ask items collected at all, mirroring `BashTool._classify`). Otherwise, every path that
    evaluates to `"ask"` becomes its own `PermissionAskItem`; if any exist, raises
    `MultiPermissionAskRequired` with all of them. With nothing to ask about, succeeds with a
    `"granted:<path1>,<path2>,..."` marker. Used only in tests.
    """

    def name(self) -> str:
        return "ask_multi_permission"

    def description(self) -> str:
        return "Test-only tool: evaluates several paths against the real permission tables."

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["paths"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        paths = [Path(p) for p in args["paths"]]
        ask_items: list[PermissionAskItem] = []
        for path in paths:
            verdict = evaluate_write(self.context, path)
            if verdict == "deny":
                raise PermissionError(f"Permission denied: write to {path}")
            if verdict == "ask":
                ask_items.append(PermissionAskItem(
                    f"write to {path}", resource=PathResource(path=path, is_write=True)))
        if ask_items:
            raise MultiPermissionAskRequired(
                "Permission requires confirmation for multiple resources", items=ask_items)
        return "granted:" + ",".join(str(p) for p in paths)
