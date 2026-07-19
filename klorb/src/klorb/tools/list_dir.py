# © Copyright 2026 Aaron Kimball
"""A Tool that enumerates the subdirectories and files of a directory for a model, so an agent
can orient itself in the local filesystem."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_read
from klorb.tools.tool import Tool

logger = logging.getLogger(__name__)


class ListDirTool(Tool):
    """Lists the immediate subdirectories and files of a directory, each as a bare name (not a
    full path) sorted alphabetically.

    `dirname` is resolved and checked against `readDirs` (see
    `klorb.permissions.workspace.resolve_and_evaluate_read`) before the directory is opened —
    confined to `SessionConfig.workspace.path` unless `SessionConfig.workspace.trusted`,
    exactly like `ReadFileTool`. A relative `dirname` is resolved against the workspace root,
    not the process's current working directory. An empty string means the workspace root
    itself. The result's `child_count` is `len(subdirs) + len(files)`, so a model doesn't have
    to count either list itself just to know how many entries a directory has.
    """

    def name(self) -> str:
        return "ListDir"

    def category(self) -> str:
        return "FILES"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Lists the immediate subdirectories and files of a directory, so you can orient "
            "yourself in the local filesystem. Returns the canonical path actually listed "
            "plus a 'subdirs' list and a 'files' list, each containing bare names "
            "(not full paths) sorted alphabetically, and 'child_count' (their combined size). "
            "A relative dirname is resolved against "
            "the project root, not any other current directory. Pass an empty string to list "
            "the project root itself. (NOTE: To locate a specific file, use `FindFile` instead!) "
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "dirname": {
                    "type": "string",
                    "description": (
                        "Path of the directory to list, relative to the project root unless "
                        "absolute. An empty string means the project root itself."
                    ),
                },
            },
            "required": ["dirname"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            dirname = args["dirname"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'dirname'. Provide the path of the directory to list.")
        logger.debug("ListDir %r", dirname)

        path, verdict = resolve_and_evaluate_read(self.context, dirname)
        raise_if_not_allowed(
            verdict, resource_description=f"list directory {path}", path=path, is_write=False)

        if not path.exists():
            raise FileNotFoundError(f"{dirname!r} does not exist")
        if not path.is_dir():
            raise NotADirectoryError(f"{dirname!r} is not a directory")

        subdirs = []
        files = []
        for entry in path.iterdir():
            if entry.is_dir():
                subdirs.append(entry.name)
            else:
                files.append(entry.name)
        subdirs.sort()
        files.sort()

        logger.debug(
            "ListDir %s found %d subdir(s), %d file(s)", path, len(subdirs), len(files))

        return {
            "cwd": str(path),
            "subdirs": subdirs,
            "files": files,
            "child_count": len(subdirs) + len(files),
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        dirname = args.get("dirname", "?")
        if error is not None:
            return f"List dir: {dirname} failed: {error}"
        if not isinstance(result, dict):
            return f"List dir: {dirname}"
        return (
            f"List dir: {result.get('cwd', dirname)} "
            f"({len(result.get('subdirs', []))} dirs, {len(result.get('files', []))} files)"
        )
