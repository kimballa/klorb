# © Copyright 2026 Aaron Kimball
"""A Tool that replaces a verified, inclusive line range in a memory file for a model."""

import logging
from collections.abc import Sequence
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.tools.memory.common import (
    NAMESPACE_SCHEMA_PROPERTY,
    memory_namespace_dir,
    memory_toc_overflow_warning,
    require_workspace_namespace_accessible,
    validate_memory_filename,
)
from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import DiffPreview, Tool, truncate_lines
from klorb.tools.util import DiffHunk, EditFileCore, format_edit_result

logger = logging.getLogger(__name__)


class EditMemoryTool(Tool):
    """Replaces a block of a memory file's current content with `new_text`.

    A memory's first line is its topic and must never end up blank. A nonexistent memory
    doesn't need a separate `CreateMemory` call first: `old_text=""` auto-creates it. Any
    other shape against a nonexistent memory raises `FileNotFoundError`.

    A `workspace`-namespace edit is gated by `tools.memory.writePermission`; a `global`-
    namespace edit is always allowed. Editing `MEMORY.md` to 45+ lines attaches a
    `SystemInterjection` urging compaction.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.edit_file_core = EditFileCore()

    def name(self) -> str:
        return "EditMemory"

    def aliases(self) -> Sequence[str]:
        return ("EditMemories", "MemoryEdit")

    def category(self) -> str:
        return "MEMORY"

    def is_read_only(self) -> bool:
        return False

    def description(self) -> str:
        return (
            "Replaces a block of a memory file's current content with new_text -- same "
            "mechanics as EditFile; see your system prompt's guidance. A memory's first line "
            "is its topic and must never be blank -- an edit that would leave it blank fails."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "namespace": NAMESPACE_SCHEMA_PROPERTY,
                "filename": {
                    "type": "string",
                    "description": "Name of the memory file to edit, e.g. 'user-preferences.md'.",
                },
                **self.edit_file_core.parameter_properties(),
            },
            "required": ["namespace", "filename", "new_text"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            namespace = args["namespace"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'namespace'. Must be 'global' or 'workspace'.")
        try:
            filename = args["filename"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'filename'. Provide the name of the memory file.")
        logger.debug("EditMemory %s/%s", namespace, filename)

        if namespace not in ("global", "workspace"):
            raise ValueError(f"namespace must be 'global' or 'workspace', got {namespace!r}")
        require_workspace_namespace_accessible(self.context, namespace)
        if namespace == "workspace":
            raise_if_not_allowed(
                self.context.session_config.memory_write_permission,
                resource_description=f"edit {namespace} memory {filename}",
                memory=("write", filename))

        namespace_dir = memory_namespace_dir(self.context, namespace)
        path = validate_memory_filename(filename, namespace_dir)

        subject = f"{namespace} memory {filename}"
        # A missing memory is only recoverable via EditFileCore's own old_text="" sentinel --
        # otherwise apply() raises FileNotFoundError naming CreateMemory as the tool to create
        # it with first. original_content stays None in that create case, so the blank-first-
        # line recovery below deletes the freshly created file instead of trying to restore
        # content that never existed.
        original_content = path.read_text(encoding="utf-8") if path.is_file() else None
        result = self.edit_file_core.apply(
            path, args, subject=subject, reread_hint=f"re-ReadMemory {namespace}/{filename}",
            create_hint="CreateMemory")

        new_first_line = path.read_text(encoding="utf-8").splitlines()[:1]
        if not new_first_line or not new_first_line[0].strip():
            if original_content is not None:
                path.write_text(original_content, encoding="utf-8")
            else:
                logger.debug(
                    "EditMemory %s/%s undoing auto-create: first line would be blank",
                    namespace, filename)
                path.unlink(missing_ok=True)
            raise ValueError(
                f"{subject}'s first line is its topic and must not be blank; this edit would "
                "have left it blank, so it was not applied")

        result["namespace"] = namespace
        result["filename"] = filename
        logger.debug(
            "EditMemory %s/%s replaced %d line(s) at line %d of what is now a %d-line memory",
            namespace, filename, result["replaced_lines"], result["start_line"],
            result["new_total_lines"],
        )
        return result

    def update_args(
        self, tool_args: dict[str, Any], tool_response: Any, err_info: ToolCallErrorInfo,
    ) -> dict[str, Any]:
        return self.edit_file_core.update_args(tool_args, err_info)

    def format_response(self, apply_output: Any) -> str:
        return format_edit_result(apply_output)

    def call_interjection(self, result: Any) -> str | None:
        return memory_toc_overflow_warning(
            result["namespace"], result["filename"], result["new_total_lines"])

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Return a one-line summary with added/removed line counts on success."""
        namespace = args.get("namespace", "?")
        filename = args.get("filename", "?")
        diff = ""
        new_text = args.get("new_text")
        if isinstance(result, dict) and isinstance(new_text, str):
            removed = result.get("replaced_lines")
            if isinstance(removed, int):
                added = new_text.count("\n") + 1 if new_text else 0
                diff = f" (+{added}/-{removed})"
        base = f"Edit memory: {namespace}/{filename}{diff}"
        return base if error is None else f"{base} failed: {error}"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None or not isinstance(result, dict) or "post_edit_content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["post_edit_content"] = truncate_lines(result["post_edit_content"], 8)
        return super().detail_view(args, capped_result, error)

    def diff_preview(
        self, args: dict[str, Any], result: Any = None, error: str | None = None,
    ) -> DiffPreview | None:
        if error is not None or not isinstance(result, dict) or "diff" not in result:
            return None
        hunks = [DiffHunk.model_validate(hunk) for hunk in result["diff"]]
        return DiffPreview(label=self.summary(args, result, error), hunks=hunks)
