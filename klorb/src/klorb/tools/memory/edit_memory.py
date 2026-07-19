# © Copyright 2026 Aaron Kimball
"""A Tool that replaces a verified, inclusive line range in a memory file for a model."""

import logging
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.tools.memory.common import (
    NAMESPACE_SCHEMA_PROPERTY,
    memory_namespace_dir,
    require_workspace_namespace_accessible,
    validate_memory_filename,
)
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import Tool, truncate_lines
from klorb.tools.util import EditFileCore

logger = logging.getLogger(__name__)


class EditMemoryTool(Tool):
    """Replaces the inclusive line range `[start_line, end_line]` of a memory file with
    `new_text` — delegating that mechanic to `self.edit_file_core` (a
    `klorb.tools.util.EditFileCore`), the same one `EditFileTool`/`EditScratchpadTool` use. See
    your system prompt's guidance on `EditFile`/`EditScratchpad` for the `start_text`/
    `end_text`/`context_before`/`context_after` conventions, drift tolerance, and "Ambiguous
    match" handling -- identical here.

    A memory's first line is its topic (see docs/specs/memories.md's file-format rule) and must
    never end up blank. Since `EditFileCore.apply()` resolves `start_line`/`end_line` drift and
    writes the file in one step, there's no way to predict the resulting first line without
    either duplicating its drift-resolution algorithm or checking after the fact -- this
    delegates as normal, then re-reads the file's first line and, if it's now blank, either
    rewrites the file's pre-edit content back (an existing memory) or deletes it (one this call
    itself just auto-created, per the next paragraph -- there's no pre-edit content to restore),
    and raises `ValueError`, rather than leaving a memory with no topic on disk.

    A nonexistent memory doesn't need a separate `CreateMemory` call first: the empty-subject
    insert shape (`start_line=1, end_line=0, start_text="", end_text=""`) auto-creates it, same
    as `EditFileTool` -- see `EditFileCore.apply()`. Any other shape against a nonexistent
    memory raises `FileNotFoundError` naming `CreateMemory` as the tool to use instead.

    `namespace`/`filename` are validated (see `klorb.tools.memory.common.
    validate_memory_filename`) and checked against the untrusted-workspace gate and
    `tools.memory.editPermission` before any disk I/O.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.edit_file_core = EditFileCore(context.process_config.edit_file_drift_search_radius)

    def name(self) -> str:
        return "EditMemory"

    def category(self) -> str:
        return "MEMORY"

    def is_read_only(self) -> bool:
        return False

    def description(self) -> str:
        return (
            "Replaces the inclusive 1-indexed line range [start_line, end_line] of a memory "
            "file with new_text -- same mechanics as EditFile; see your system "
            "prompt's guidance. A memory's first line is its topic and "
            "must never be blank -- an edit that would leave it blank fails."
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
        logger.debug("EditMemory %s/%s (start_line=%s, end_line=%s)",
                     namespace, filename, args.get("start_line"), args.get("end_line"))

        if namespace not in ("global", "workspace"):
            raise ValueError(f"namespace must be 'global' or 'workspace', got {namespace!r}")
        require_workspace_namespace_accessible(self.context, namespace)
        raise_if_not_allowed(
            self.context.process_config.memory_edit_permission,
            resource_description=f"edit {namespace} memory {filename}")

        namespace_dir = memory_namespace_dir(self.context, namespace)
        path = validate_memory_filename(filename, namespace_dir)

        subject = f"{namespace} memory {filename}"
        # A missing memory is only recoverable via EditFileCore's own empty-subject insert
        # shape (start_line=1, end_line=0, ...) -- otherwise apply() raises FileNotFoundError
        # naming CreateMemory as the tool to create it with first. original_content stays None
        # in that create case, so the blank-first-line recovery below deletes the freshly
        # created file instead of trying to restore content that never existed.
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
            "EditMemory %s/%s replaced lines %d-%d (now %d-%d) of what is now a %d-line memory",
            namespace, filename, result["requested_start_line"], result["requested_end_line"],
            result["start_line"], result["end_line"], result["new_total_lines"],
        )
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """`"Edit memory: namespace/foo.md (+A/-R)"`, where the added/removed line counts prefer
        `result`'s `requested_start_line`/`requested_end_line` (the call's line hint after
        normalization -- alias-resolved, and, for an `old_text` call that omitted `end_line`,
        inferred from its line count) when a `result` is available, falling back to the call's
        own raw args otherwise (a failed call has no `result`)."""
        namespace = args.get("namespace", "?")
        filename = args.get("filename", "?")
        diff = ""
        if isinstance(result, dict):
            start_line, end_line = result.get("requested_start_line"), result.get("requested_end_line")
        else:
            start_line, end_line = args.get("start_line"), args.get("end_line")
        new_text = args.get("new_text")
        if isinstance(start_line, int) and isinstance(end_line, int) and isinstance(new_text, str):
            removed = end_line - start_line + 1
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
