# © Copyright 2026 Aaron Kimball
"""Tests for klorb.search_index.catalogs."""

import pytest

from klorb.search_index.catalogs import (
    MEMORIES_GLOBAL_CATALOG,
    MEMORIES_WORKSPACE_CATALOG,
    namespace_for_catalog,
)


def test_namespace_for_catalog_resolves_both_catalogs() -> None:
    assert namespace_for_catalog(MEMORIES_GLOBAL_CATALOG) == "global"
    assert namespace_for_catalog(MEMORIES_WORKSPACE_CATALOG) == "workspace"


def test_namespace_for_catalog_rejects_an_unknown_catalog() -> None:
    with pytest.raises(ValueError, match="not a memories catalog"):
        namespace_for_catalog("workspace")
