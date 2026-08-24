# © Copyright 2026 Aaron Kimball
"""The file-creation mechanic shared by `CreateFileTool` and `CreateMemoryTool` — see
`klorb.tools.util`'s package docstring for how each holds one of these as a member and
delegates to it."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.tool import NO_READFILE_VERIFICATION_NOTE
from klorb.tools.util.response_headers import format_header_lines
from klorb.tools.util.secret_redaction import SecretRedactor

if TYPE_CHECKING:
    from klorb.session import Session

_CREATE_RESULT_HEADER_ORDER = (
    "namespace", "filename",
    "created", "total_lines", "warning", "note",
)
"""Key order `format_create_result()` renders a `CreateFileCore.apply()`-shaped result dict's
header lines in."""


def format_create_result(result: dict[str, Any]) -> str:
    """Render a `CreateFileCore.apply()`-shaped result dict as `key: value` header lines in
    `_CREATE_RESULT_HEADER_ORDER`, followed by `content` as a plain-text block."""
    header_lines = format_header_lines(
        result, _CREATE_RESULT_HEADER_ORDER, known_elsewhere=frozenset({"content"}))
    return "\n".join(header_lines) + "\n\nCreated content:\n========\n" + result["content"]


class CreateFileCore:
    """Creates a new text file at `path` with the given content, raising `FileExistsError` if
    it already exists — the shared mechanic behind `CreateFileTool` and `CreateMemoryTool`.
    Missing parent directories are created automatically.
    """

    def update_args(self, tool_args: dict[str, Any], err_info: ToolCallErrorInfo) -> dict[str, Any]:
        """`tool_args.content` dropped once the call succeeded, since `apply()`'s own
        `content` already reflects it; unchanged on error."""
        if err_info.is_error:
            return tool_args
        new_args = dict(tool_args)
        # We can't *entirely* remove the field or it confuses the agent to read it back; replace
        # the actual lengthy contents with a short update.
        new_args["content"] = "(Applied correctly; arguments truncated. See response)"
        return new_args

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

    def apply(
        self, path: Path, args: dict[str, Any], *, subject: str, edit_hint: str,
        redactor: SecretRedactor | None = None, session: "Session | None" = None,
    ) -> dict[str, Any]:
        """Create `path` with `args["content"]`, returning `total_lines`, `created`, `note`, and
        `content` (the caller adds `filename` if it has one).

        `subject` names the thing being created, for the "already exists" error message (e.g.
        a filename, or a memory's namespace/filename pair); `edit_hint` names the tool to use
        instead (e.g. `"EditFile"` or `"EditMemory"`).

        When `redactor` is given, `content` is detokenized before writing -- so a
        `[[SECRET:...]]` token echoed from an earlier `ReadFile` resolves to the file's real
        bytes rather than being written literally. The returned `content` is then re-redacted so
        the result never carries a plaintext secret either. See
        docs/specs/secret-redaction.md.
        """
        redacted_content = args["content"]
        if redactor is not None:
            content = redactor.detokenize(session, redacted_content)
        else:
            content = redacted_content
        if path.exists():
            raise FileExistsError(f"{subject} already exists; use {edit_hint} to modify it instead")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        new_lines = content.splitlines()
        return {
            "total_lines": len(new_lines),
            "created": True,
            "content": redacted_content,
            "note": NO_READFILE_VERIFICATION_NOTE,
        }
