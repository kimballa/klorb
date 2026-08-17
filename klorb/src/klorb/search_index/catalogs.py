# © Copyright 2026 Aaron Kimball
"""Search-index catalog name constants shared by `indexer.py` and `memory_indexer.py`, kept in
their own module so neither has to import the other for them.
"""

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
