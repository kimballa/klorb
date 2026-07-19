# © Copyright 2026 Aaron Kimball
"""A Tool that reads a range of lines from one of a skill's supporting files, confined to the
skill's own directory and gated by the skill's `skillRules` verdict."""

import logging
from typing import Any

from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.skill.catalog import get_skill_catalog_registry, resolve_and_gate_skill
from klorb.tools.skill.common import NAMESPACE_SCHEMA_PROPERTY, resolve_skill_file
from klorb.tools.tool import Tool, truncate_lines
from klorb.tools.util import ReadFileCore

logger = logging.getLogger(__name__)


class ReadSkillFileTool(Tool):
    """Reads up to `max_lines` lines from a supporting file bundled with a skill (one of the
    relative paths in `ActivateSkill`'s `files` manifest), via `self.read_file_core` (a
    `klorb.tools.util.ReadFileCore`).

    `namespace`/`name` are resolved as in `ActivateSkill`, and `path` is confined to the skill
    directory (relative, no `..`, within the directory after symlink resolution). Reading is gated
    by the skill's `skillRules` verdict -- the same gate as `ActivateSkill`, raising no second ask.
    See docs/specs/skills.md.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self.read_file_core = ReadFileCore(context.process_config.read_file_max_lines)

    def name(self) -> str:
        return "ReadSkillFile"

    def category(self) -> str:
        return "SKILL"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Reads a supporting file bundled with a skill (one of the relative paths in "
            f"ActivateSkill's 'files' manifest) and returns up to {self.read_file_core.max_lines} "
            "of its lines, each prefixed with its 1-indexed line number followed by '|', same as "
            "ReadFile. Give the skill's namespace and name plus the file's path relative to the "
            "skill directory. Use start_line and end_line to page through a file larger than the "
            "per-call limit."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "namespace": NAMESPACE_SCHEMA_PROPERTY,
                "name": {
                    "type": "string",
                    "description": "The skill's name (its directory basename), e.g. 'add-cli-flag'.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Path of the file to read, relative to the skill directory, e.g. "
                        "'reference/schema.md' — one of the entries in ActivateSkill's manifest."
                    ),
                },
                **self.read_file_core.parameter_properties(),
            },
            "required": ["namespace", "name", "path"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            namespace = args["namespace"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'namespace'. Must be 'workspace', 'user', or 'internal'.")
        try:
            name = args["name"]
        except KeyError:
            raise ValueError("Missing required argument: 'name'. Provide the skill's name.")
        try:
            path = args["path"]
        except KeyError:
            raise ValueError(
                "Missing required argument: 'path'. Provide the file path relative to the skill.")
        if not isinstance(path, str):
            raise ValueError(f"path must be a string, got {path!r}")
        logger.debug("ReadSkillFile %s/%s %s", namespace, name, path)

        registry = get_skill_catalog_registry()
        registry.ensure_from_context(self.context)
        resolved = resolve_and_gate_skill(
            catalog=registry.canonical(), skill_rules=self.context.session_config.skill_rules,
            override=self.context.permission_override, namespace=namespace, name=name)

        target = resolve_skill_file(resolved, path)
        result = self.read_file_core.apply(target, args)
        result["namespace"] = resolved.namespace
        result["name"] = resolved.name
        result["path"] = path
        logger.debug(
            "ReadSkillFile %s/%s %s returned lines %d-%d of %d (truncated=%s)",
            resolved.namespace, resolved.name, path, result["start_line"], result["end_line"],
            result["total_lines"], result["truncated"],
        )
        return result

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        namespace = args.get("namespace", "?")
        name = args.get("name", "?")
        path = args.get("path", "?")
        if error is not None:
            return f"Read skill file: {namespace}/{name}:{path} failed: {error}"
        if not isinstance(result, dict):
            return f"Read skill file: {namespace}/{name}:{path}"
        return (
            f"Read skill file: {result.get('namespace', namespace)}/{result.get('name', name)}:"
            f"{result.get('path', path)} "
            f"(lines {result.get('start_line')}-{result.get('end_line')} of {result.get('total_lines')})"
        )

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["content"] = truncate_lines(result["content"], 8)
        return super().detail_view(args, capped_result, error)
