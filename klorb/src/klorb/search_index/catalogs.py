# © Copyright 2026 Aaron Kimball
"""Search-index catalog name constants shared by `indexer.py` and the tools that query them, kept
in their own module so neither has to import the other for them.
"""

from pathlib import PurePosixPath

from klorb.permissions.directory_access import KLORB_PROJECT_DIR_NAME
from klorb.permissions.skill_access import Namespace

MEMORIES_GLOBAL_CATALOG = "memories-global"
MEMORIES_WORKSPACE_CATALOG = "memories-workspace"


def namespace_for_catalog(catalog: str) -> str:
    """Return the memory namespace (`"global"`/`"workspace"`) `catalog` belongs to. Raises
    `ValueError` if `catalog` isn't one of the two memories catalogs."""
    if catalog == MEMORIES_GLOBAL_CATALOG:
        return "global"
    if catalog == MEMORIES_WORKSPACE_CATALOG:
        return "workspace"
    raise ValueError(f"{catalog!r} is not a memories catalog")


SKILLS_USER_CATALOG = "skills-user"
SKILLS_WORKSPACE_CATALOG = "skills-workspace"
SKILLS_INTERNAL_CATALOG = "skills-internal"

_SKILLS_CATALOG_BY_NAMESPACE: dict[Namespace, str] = {
    "user": SKILLS_USER_CATALOG,
    "workspace": SKILLS_WORKSPACE_CATALOG,
    "internal": SKILLS_INTERNAL_CATALOG,
}
_SKILLS_NAMESPACE_BY_CATALOG: dict[str, Namespace] = {
    catalog: namespace for namespace, catalog in _SKILLS_CATALOG_BY_NAMESPACE.items()
}

SKILLS_VIRTUAL_DIRNAME = f"{KLORB_PROJECT_DIR_NAME}/skills-index"
"""Synthetic virtual directory standing in for every skill tier's real root in `Chunk.source_path`
and the `files` table's bookkeeping key, the same trick `indexer._GLOBAL_MEMORIES_VIRTUAL_DIRNAME`
uses -- applied uniformly to all three skill tiers (even the real, on-disk `workspace` one) so a
chunk's skill identity is always recovered the same way regardless of which tier it came from."""


def skills_catalog_for_namespace(namespace: Namespace) -> str:
    """Return the skills catalog (`SKILLS_USER_CATALOG`/`SKILLS_WORKSPACE_CATALOG`/
    `SKILLS_INTERNAL_CATALOG`) `namespace` indexes into."""
    return _SKILLS_CATALOG_BY_NAMESPACE[namespace]


def skill_namespace_for_catalog(catalog: str) -> Namespace:
    """Return the skill namespace (`"user"`/`"workspace"`/`"internal"`) `catalog` belongs to.
    Raises `ValueError` if `catalog` isn't one of the three skills catalogs."""
    namespace = _SKILLS_NAMESPACE_BY_CATALOG.get(catalog)
    if namespace is None:
        raise ValueError(f"{catalog!r} is not a skills catalog")
    return namespace


def skill_source_path(skill_name: str, relative_path: str) -> str:
    """The synthetic `Chunk.source_path` for `relative_path` within skill `skill_name`."""
    return f"{SKILLS_VIRTUAL_DIRNAME}/{skill_name}/{relative_path}"


def skill_name_from_source_path(source_path: str) -> str:
    """Recover a skill's `name` from a `Chunk.source_path` built by `skill_source_path`."""
    return PurePosixPath(source_path).relative_to(SKILLS_VIRTUAL_DIRNAME).parts[0]
