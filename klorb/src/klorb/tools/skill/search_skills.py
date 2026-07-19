# © Copyright 2026 Aaron Kimball
"""A Tool that narrows the available-skills list by keyword, matching each query as a literal,
case-insensitive substring against a skill's name and its full SKILL.md body."""

import logging
from typing import Any

from klorb.tools.skill.catalog import canonical_catalog, ensure_skill_catalog
from klorb.tools.skill.common import read_skill_md
from klorb.tools.tool import Tool
from klorb.tools.util import compile_queries, validate_queries

logger = logging.getLogger(__name__)


class SearchSkillsTool(Tool):
    """Searches every discoverable skill for any of `queries`, each matched as a literal,
    case-insensitive substring against both the skill's `name` and its full `SKILL.md` body.
    Returns a flat list of `{namespace, name, description}` for every skill with a hit.

    Respects the workspace-trust gate and excludes any skill whose `(namespace, name)` evaluates to
    `"deny"`. See docs/specs/skills.md.
    """

    def name(self) -> str:
        return "SearchSkills"

    def description(self) -> str:
        return (
            "Narrows the list of available skills by keyword. Searches every discoverable skill "
            "for the given search strings, each matched as a literal, case-insensitive substring "
            "(not a regular expression) against both the skill's name and its full SKILL.md "
            "content. Returns a list under 'results', each entry '{namespace, name, description}' "
            "for a skill with at least one hit. Use this when there are enough skills that the "
            "standing available-skills list alone is hard to scan; then load a matched skill with "
            "ActivateSkill(namespace=..., name=...)."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": (
                        "One or more literal search strings, matched case-insensitively (not "
                        "regular expressions) against both skill names and SKILL.md content."
                    ),
                },
            },
            "required": ["queries"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        try:
            queries = validate_queries(args["queries"])
        except KeyError:
            raise ValueError(
                "Missing required argument: 'queries'. Provide a non-empty array of search strings.")
        logger.debug("SearchSkills %r", queries)

        compiled = compile_queries(queries, case_insensitive=True)
        workspace = self.context.session_config.workspace
        ensure_skill_catalog(
            workspace_root=workspace.path, workspace_trusted=workspace.trusted,
            claude_skills_compat=self.context.process_config.compatibility_claude_skills)

        results: list[dict[str, str]] = []
        for skill in canonical_catalog().discoverable(self.context.session_config.skill_rules):
            try:
                body = read_skill_md(skill)
            except (OSError, UnicodeDecodeError):
                body = ""
            if compiled.search(skill.name) or compiled.search(body):
                results.append({
                    "namespace": skill.namespace,
                    "name": skill.name,
                    "description": skill.description,
                })

        logger.debug("SearchSkills found %d skill(s)", len(results))
        return {"queries": queries, "match_count": len(results), "results": results}

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        queries = args.get("queries", "?")
        if error is not None:
            return f"Search skills: {queries!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Search skills: {queries!r}"
        count = result.get("match_count", 0)
        plural = "es" if count != 1 else ""
        return f"Search skills: {queries!r} ({count} match{plural})"
