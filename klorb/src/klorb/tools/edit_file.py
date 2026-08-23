# © Copyright 2026 Aaron Kimball
"""A Tool that replaces a verified, inclusive line range in a text file for a model."""

import logging
from collections.abc import Sequence
from typing import Any

from klorb.permissions.table import raise_if_not_allowed
from klorb.permissions.workspace import resolve_and_evaluate_write
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tool import NO_READFILE_VERIFICATION_NOTE, DiffPreview, Tool, truncate_lines
from klorb.tools.util import DiffHunk, EditFileCore, format_edit_result, get_or_create_secret_redactor

logger = logging.getLogger(__name__)


class EditFileTool(Tool):
    """Replaces a block of a text file's current content with `new_text`.

    `filename` is checked against `writeFiles` and otherwise confined to
    `SessionConfig.workspace.path` and further checked against `writeDirs` before any disk I/O.

    A nonexistent `filename` doesn't need a separate `CreateFile` call first: `old_text=""`
    creates it directly, including any missing parent directories.
    Any other shape against a nonexistent file raises `FileNotFoundError` naming `CreateFile` as
    the tool to use instead.

    `self._secret_redactor` resolves any `[[SECRET:<type>:<hash>]]` token in the call's
    arguments back to real plaintext before matching/writing, and re-masks the result before
    it's returned.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.edit_file_core = EditFileCore()
        self._secret_redactor = get_or_create_secret_redactor(context.session)

    def name(self) -> str:
        return "EditFile"

    def aliases(self) -> Sequence[str]:
        return ("FileEdit",)

    def category(self) -> str:
        return "FILES"

    def is_read_only(self) -> bool:
        return False

    def description(self) -> str:
        return (
            "Replaces a block of a text file's current content with new_text, located by "
            "old_text (or old_text_start/old_text_end) rather than a line number.\n"
            "See your system prompt's guidance on EditFile for the empty-file/insert/delete "
            "conventions and 'Ambiguous match' errors.\n"
            "The response includes a `post_edit_content` field showing the edited region "
            "with line numbers, and a total line count in `new_total_lines` "
            "— no follow-up ReadFile is needed to verify."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Path to the text file to edit.",
                },
                **self.edit_file_core.parameter_properties(),
            },
            "required": ["filename", "new_text"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            filename = args["filename"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'filename'. Provide the path of the file to edit.")
        logger.debug("EditFile %s", filename)

        path, verdict = resolve_and_evaluate_write(self.context, filename)
        raise_if_not_allowed(
            verdict, resource_description=f"write to {path}", path=path, is_write=True)

        result = self.edit_file_core.apply(
            path, args, subject=filename, reread_hint=f"Use ReadFile on filename={filename}",
            create_hint="CreateFile", redactor=self._secret_redactor, session=self.context.session)
        result["filename"] = filename
        result["note"] = NO_READFILE_VERIFICATION_NOTE

        logger.debug(
            "EditFile %s replaced %d line(s) at line %d of what is now a %d-line file",
            filename, result["replaced_lines"], result["start_line"], result["new_total_lines"],
        )
        return result

    def format_response(self, apply_output: Any) -> str:
        return format_edit_result(apply_output)

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """`"Edit file: foo.py (+A/-R)"`, where the added/removed line counts come from
        `result`'s `replaced_lines` and the call's `new_text`."""
        filename = args.get("filename", "?")
        diff = ""
        new_text = args.get("new_text")
        if isinstance(result, dict) and isinstance(new_text, str):
            removed = result.get("replaced_lines")
            if isinstance(removed, int):
                added = new_text.count("\n") + 1 if new_text else 0
                diff = f" (+{added}/-{removed})"
        base = f"Edit file: {filename}{diff}"
        return base if error is None else f"{base} failed: {error}"

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        """Same as the default pretty-JSON rendering, but with `result["post_edit_content"]`
        capped to 8 lines."""
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
