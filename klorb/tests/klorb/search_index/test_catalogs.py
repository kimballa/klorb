# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.catalogs."""

import pytest

from klorb.search_index.catalogs import (
    MEMORIES_GLOBAL_CATALOG,
    MEMORIES_WORKSPACE_CATALOG,
    SKILLS_INTERNAL_CATALOG,
    SKILLS_USER_CATALOG,
    SKILLS_WORKSPACE_CATALOG,
    namespace_for_catalog,
    skill_name_from_source_path,
    skill_namespace_for_catalog,
    skill_source_path,
    skills_catalog_for_namespace,
)


def test_namespace_for_catalog_resolves_both_catalogs() -> None:
    assert namespace_for_catalog(MEMORIES_GLOBAL_CATALOG) == "global"
    assert namespace_for_catalog(MEMORIES_WORKSPACE_CATALOG) == "workspace"


def test_namespace_for_catalog_rejects_an_unknown_catalog() -> None:
    with pytest.raises(ValueError, match="not a memories catalog"):
        namespace_for_catalog("workspace")


def test_skills_catalog_for_namespace_resolves_all_three_tiers() -> None:
    assert skills_catalog_for_namespace("user") == SKILLS_USER_CATALOG
    assert skills_catalog_for_namespace("workspace") == SKILLS_WORKSPACE_CATALOG
    assert skills_catalog_for_namespace("internal") == SKILLS_INTERNAL_CATALOG


def test_skill_namespace_for_catalog_is_the_inverse_of_skills_catalog_for_namespace() -> None:
    for namespace in ("user", "workspace", "internal"):
        catalog = skills_catalog_for_namespace(namespace)
        assert skill_namespace_for_catalog(catalog) == namespace


def test_skill_namespace_for_catalog_rejects_an_unknown_catalog() -> None:
    with pytest.raises(ValueError, match="not a skills catalog"):
        skill_namespace_for_catalog(MEMORIES_GLOBAL_CATALOG)


def test_skill_name_from_source_path_recovers_the_skill_name() -> None:
    source_path = skill_source_path("add-cli-flag", "resources/notes.md")
    assert skill_name_from_source_path(source_path) == "add-cli-flag"
