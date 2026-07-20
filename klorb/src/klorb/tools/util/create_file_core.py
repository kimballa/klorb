# © Copyright 2026 Aaron Kimball
"""The file-creation mechanic shared by `CreateFileTool` and `CreateMemoryTool` — see
`klorb.tools.util`'s package docstring for how each holds one of these as a member and
delegates to it."""

from pathlib import Path
from typing import Any

from klorb.tools.util.diff_lines import build_diff_hunks


class CreateFileCore:
    """Creates a new text file at `path` with the given content, raising `FileExistsError` if
    it already exists — the shared mechanic behind `CreateFileTool` and `CreateMemoryTool`.
    Missing parent directories are created automatically.
    """

    def parameter_properties(self) -> dict[str, Any]:
        """Return the `content` JSON-schema property shared by `CreateFileTool` and
        `CreateMemoryTool`'s `parameters()` — each adds its own `filename` property (or not)
        and `required` list around this."""
        return {
            "content": {
                "type": "string",
                "description": "Contents of the new file. May be an empty string.",
            },
        }

    def apply(self, path: Path, args: dict[str, Any], *, subject: str, edit_hint: str) -> dict[str, Any]:
        """Create `path` with `args["content"]`, returning `total_lines`, `created`, and `diff` --
        a jsonable rendering of `klorb.tools.util.diff_lines.build_diff_hunks()`'s all-insert
        diff against an empty old subject, for a `Tool`'s `diff_preview()` to parse back into
        `DiffHunk`s (the caller adds `filename` if it has one).

        `subject` names the thing being created, for the "already exists" error message (e.g.
        a filename, or a memory's namespace/filename pair); `edit_hint` names the tool to use
        instead (e.g. `"EditFile"` or `"EditMemory"`).
        """
        content = args["content"]
        if path.exists():
            raise FileExistsError(f"{subject} already exists; use {edit_hint} to modify it instead")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        new_lines = content.splitlines()
        diff_hunks = build_diff_hunks([], new_lines)
        return {
            "total_lines": len(new_lines),
            "created": True,
            "diff": [hunk.model_dump() for hunk in diff_hunks],
        }
