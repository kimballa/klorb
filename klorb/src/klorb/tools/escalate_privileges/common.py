# © Copyright 2026 Aaron Kimball
"""Data types and the `EscalatePrivilegesRequired` exception shared between
`klorb.tools.escalate_privileges.escalate_privileges.EscalatePrivilegesTool` and
`klorb.session` — kept in a leaf module with no `klorb.tools.tool`/`klorb.tools.setup_context`
import so `klorb.session` can import it at module level without a cycle.
"""

from typing import Literal

Scope = Literal["workspace"]
"""The only valid scope value for `EscalatePrivilegesTool`. Currently only supports "workspace"."""


class EscalatePrivilegesRequired(Exception):
    """Raised by `EscalatePrivilegesTool.apply()` when the user needs to approve escalation.
    Carries the scope that was requested, for `Session._run_tool_calls` to ask about via
    `on_escalate_privileges`.
    """

    def __init__(self, scope: Scope) -> None:
        super().__init__(f"Escalate privileges for {scope}")
        self.scope = scope


def validate_scope(scope: object) -> Scope:
    """Validate that `scope` is a supported scope value. Raises `ValueError` with a model-actionable
    message if invalid."""
    if scope == "workspace":
        return "workspace"
    raise ValueError(
        f"Invalid scope '{scope}'. The only valid scope is 'workspace'."
    )
