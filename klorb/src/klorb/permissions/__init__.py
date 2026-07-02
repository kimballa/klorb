# © Copyright 2026 Aaron Kimball
"""General-purpose permission tables governing what resources klorb's tools may access.

`table.py` holds the resource-agnostic `PermissionsTable` abstraction (deny/ask/allow rule
lists, evaluated in that fixed category order); `directory_access.py` holds the first concrete
resource kind, directory access. See docs/specs/permissions.md.
"""
