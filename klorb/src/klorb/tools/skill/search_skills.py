# © Copyright 2026 Aaron Kimball
"""A Tool that narrows the available-skills list by keyword, matching each query as a literal,
case-insensitive substring against a skill's name and every markdown file in its directory, plus
high-confidence semantic hits from the skills search index."""

import logging
import re
from collections.abc import Sequence
from typing import Any, cast

from klorb.permissions.skill_access import VALID_NAMESPACES, Namespace
from klorb.search_index.catalogs import (
    skill_name_from_source_path,
    skill_namespace_for_catalog,
    skills_catalog_for_namespace,
)
from klorb.search_index.chunk import Chunk
from klorb.search_index.ranking import DEFAULT_RRF_K
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.skill.catalog import resolve_session_skill_catalog_registry
from klorb.tools.skill.common import display_skill_description, resolve_skill_file, skill_file_manifest
from klorb.tools.skill.model import Skill
from klorb.tools.tool import Tool
from klorb.tools.util import (
    HybridSearchable,
    SemanticSearchCore,
    compile_queries,
    get_or_create_secret_redactor,
    validate_queries,
)

logger = logging.getLogger(__name__)

_VALID_NAMESPACE_FILTERS = frozenset({*VALID_NAMESPACES, "all"})

SEMANTIC_TOP_K = 5
"""Cap on distinct skills a semantic hit contributes. A session isn't expected to accumulate many
skills, so a handful of confident hits is enough."""

SEMANTIC_LIMIT_PER_QUERY = 10

SEMANTIC_MIN_SCORE = 1.0 / (DEFAULT_RRF_K + 3)
"""Minimum fused RRF score a semantic hit must clear to surface. Equivalent to ranking in the top
3 of at least one of the lexical/vector lists, a high bar appropriate for a small skill collection."""


def _validate_namespace_filter(raw: Any) -> str:
    if raw in (None, ""):
        return "all"
    if raw not in _VALID_NAMESPACE_FILTERS:
        raise ValueError(
            f'namespace must be one of {sorted(_VALID_NAMESPACE_FILTERS)}, got {raw!r}')
    return cast(str, raw)


def _skill_matches(compiled: re.Pattern[str], skill: Skill) -> bool:
    """Whether `skill`'s name or any markdown file in its directory (`SKILL.md` and any nested
    resource file) matches `compiled`."""
    if compiled.search(skill.name):
        return True
    for relative_path in skill_file_manifest(skill):
        if not relative_path.endswith(".md"):
            continue
        try:
            body = resolve_skill_file(skill, relative_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if compiled.search(body):
            return True
    return False


class SearchSkillsTool(Tool):
    """Searches every discoverable skill for any of `queries`, each matched as a literal,
    case-insensitive substring against the skill's `name` and every markdown file in its
    directory, plus high-confidence semantic hits when a skills search index is available.
    `namespace` optionally restricts the search to `internal`, `user`, or `workspace`; defaults to
    `all`. Returns a flat list of `{namespace, name, description}` for every skill with a hit --
    never file content or a path, since the only way to read a skill's content is
    `ActivateSkill`.

    Respects the workspace-trust gate and excludes any skill whose `(namespace, name)` evaluates to
    `"deny"`. See docs/specs/skills.md.
    """

    def __init__(self, context: ToolSetupContext) -> None:
        super().__init__(context)
        self._core = SemanticSearchCore(
            max_line_length=context.process_config.grep_max_line_length,
            secret_redactor=get_or_create_secret_redactor(context.session))

    def name(self) -> str:
        return "SearchSkills"

    def aliases(self) -> Sequence[str]:
        return ("SkillSearch",)

    def category(self) -> str:
        return "SKILL"

    def is_read_only(self) -> bool:
        return True

    def description(self) -> str:
        return (
            "Narrows the list of available skills by keyword. Searches every discoverable skill "
            "for the given search strings, each matched as a literal, case-insensitive substring "
            "against the skill's name and every markdown file inside, "
            "plus high-confidence semantic hits. Returns a `results` list, each "
            "entry `{namespace, name, description}` for a skill with at least one hit. "
            "Optionally restrict the search to one namespace with namespace (\"internal\", "
            "\"user\", or \"workspace\"); defaults to \"all\". Use this when the standing "
            "available skills list is hard to scan; then load a matched skill with "
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
                        "One or more literal search strings, matched case-insensitively "
                        "against both skill names and their markdown content."
                    ),
                },
                "namespace": {
                    "type": "string",
                    "enum": [*VALID_NAMESPACES, "all"],
                    "description": (
                        "Restrict the search to one namespace: \"internal\" (klorb's built-in "
                        "skills), \"user\" (the per-user skills directory), or \"workspace\" "
                        "(this project). Defaults to \"all\"."
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
        namespace_filter = _validate_namespace_filter(args.get("namespace"))
        logger.debug("SearchSkills %r (namespace=%s)", queries, namespace_filter)

        compiled = compile_queries(queries, case_insensitive=True)
        registry = resolve_session_skill_catalog_registry(self.context)
        namespaces = self._namespaces_to_search(namespace_filter)

        candidates = [
            skill for skill in registry.canonical().discoverable(self.context.session_config.skill_rules)
            if skill.namespace in namespaces
        ]
        candidates_by_id: dict[tuple[str, str], Skill] = {
            (skill.namespace, skill.name): skill for skill in candidates
        }

        results: list[dict[str, str]] = []
        already_reported: set[tuple[str, str]] = set()
        for skill in candidates:
            if _skill_matches(compiled, skill):
                results.append(self._render_skill(skill))
                already_reported.add((skill.namespace, skill.name))

        for skill in self._semantic_hits(namespaces, queries, candidates_by_id, already_reported):
            results.append(self._render_skill(skill))

        logger.debug("SearchSkills found %d skill(s)", len(results))
        return {"queries": queries, "match_count": len(results), "results": results}

    def _namespaces_to_search(self, namespace_filter: str) -> tuple[Namespace, ...]:
        return VALID_NAMESPACES if namespace_filter == "all" else (cast(Namespace, namespace_filter),)

    def _render_skill(self, skill: Skill) -> dict[str, str]:
        return {
            "namespace": skill.namespace,
            "name": skill.name,
            "description": display_skill_description(skill.description),
        }

    def _semantic_hits(
        self, namespaces: Sequence[Namespace], queries: list[str],
        candidates_by_id: dict[tuple[str, str], Skill], already_reported: set[tuple[str, str]],
    ) -> list[Skill]:
        """Up to `SEMANTIC_TOP_K` distinct skills, scoring at least `SEMANTIC_MIN_SCORE`, from
        whichever of `namespaces`' skills catalogs `session.workspace_indexer` currently covers.
        A hit for a skill no longer in `candidates_by_id` (removed, shadowed, or denied since the
        index was last scanned) or already in `already_reported` is dropped silently."""
        indexer = self.context.session.workspace_indexer if self.context.session is not None else None
        if indexer is None:
            return []
        indexers: list[tuple[HybridSearchable, str]] = [
            (indexer, skills_catalog_for_namespace(namespace)) for namespace in namespaces
        ]
        hits = self._core.merged_hits(
            indexers, queries, limit_per_query=SEMANTIC_LIMIT_PER_QUERY, min_score=SEMANTIC_MIN_SCORE)
        hits.sort(key=lambda pair: pair[1], reverse=True)

        matched: list[Skill] = []
        for chunk, _score in hits:
            skill_id = self._skill_id_for_chunk(chunk)
            if skill_id in already_reported:
                continue
            skill = candidates_by_id.get(skill_id)
            if skill is None:
                continue
            matched.append(skill)
            already_reported.add(skill_id)
            if len(matched) >= SEMANTIC_TOP_K:
                break
        return matched

    def _skill_id_for_chunk(self, chunk: Chunk) -> tuple[str, str]:
        return skill_namespace_for_catalog(chunk.catalog), skill_name_from_source_path(chunk.source_path)

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        queries = args.get("queries", "?")
        if error is not None:
            return f"Search skills: {queries!r} failed: {error}"
        if not isinstance(result, dict):
            return f"Search skills: {queries!r}"
        count = result.get("match_count", 0)
        plural = "es" if count != 1 else ""
        return f"Search skills: {queries!r} ({count} match{plural})"
