# © Copyright 2026 Aaron Kimball
"""A Tool that loads a skill's full SKILL.md instructions (and a manifest of its supporting files)
into the model's context, gated by the skill's `skillRules` verdict."""

import logging
from typing import Any

from klorb.tools.skill.common import (
    NAMESPACE_SCHEMA_PROPERTY,
    read_skill_md,
    resolve_and_gate_skill,
    skill_file_manifest,
)
from klorb.tools.tool import Tool, truncate_lines

logger = logging.getLogger(__name__)


class ActivateSkillTool(Tool):
    """Resolves a `(namespace, name)` pair to a skill and returns its full `SKILL.md` content plus
    a sorted manifest of every file in the skill directory (paths relative to it). The model
    follows those instructions and reaches any supporting file through `ReadSkillFile`.

    Gated by the skill's `skillRules` verdict: `"deny"` raises, `"ask"` interrupts for approval,
    `"allow"` proceeds. An unknown `(namespace, name)` is a plain `ValueError`. See
    docs/specs/skills.md.
    """

    def name(self) -> str:
        return "ActivateSkill"

    def description(self) -> str:
        return (
            "Loads a skill's full instructions into your context so you can follow them. Give the "
            "skill's namespace and name (as listed in the available-skills interjection or a "
            "SearchSkills result). Returns the skill's SKILL.md 'content' and a 'files' manifest "
            "of every supporting file in the skill directory (paths relative to it) — read one of "
            "those with ReadSkillFile. The first time you activate a given skill you may be asked "
            "to approve loading it."
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
            },
            "required": ["namespace", "name"],
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
        logger.debug("ActivateSkill %s/%s", namespace, name)

        workspace = self.context.session_config.workspace
        resolved = resolve_and_gate_skill(
            workspace_root=workspace.path, workspace_trusted=workspace.trusted,
            claude_skills_compat=self.context.process_config.compatibility_claude_skills,
            skill_rules=self.context.session_config.skill_rules,
            override=self.context.permission_override,
            namespace=namespace, name=name)

        content = read_skill_md(resolved)
        files = skill_file_manifest(resolved)
        logger.info(
            "ActivateSkill %s/%s loaded (%d supporting file(s))",
            resolved.namespace, resolved.name, len(files))
        return {
            "namespace": resolved.namespace,
            "name": resolved.name,
            "content": content,
            "files": files,
        }

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        namespace = args.get("namespace", "?")
        name = args.get("name", "?")
        if error is not None:
            return f"Activate skill: {namespace}/{name} failed: {error}"
        if not isinstance(result, dict):
            return f"Activate skill: {namespace}/{name}"
        file_count = len(result.get("files", []))
        return (
            f"Activate skill: {result.get('namespace', namespace)}/{result.get('name', name)} "
            f"({file_count} file{'s' if file_count != 1 else ''})"
        )

    def detail_view(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None or not isinstance(result, dict) or "content" not in result:
            return super().detail_view(args, result, error)
        capped_result = dict(result)
        capped_result["content"] = truncate_lines(result["content"], 12)
        return super().detail_view(args, capped_result, error)
