# © Copyright 2026 Aaron Kimball
"""A Tool that requests elevated privileges for workspace access.

Mirrors the shape of the permission-ask mechanism (`klorb.permissions.table.
PermissionAskRequired`, `klorb.session.PermissionAskContext`/`PermissionDecision`) and
`AskUserQuestionsTool`: a `Tool`'s `apply()` can't itself block on user input (see `Tool`'s
single `ToolSetupContext` argument contract), so `EscalatePrivilegesTool.apply()` validates
its arguments and, if they're well-formed, always raises `EscalatePrivilegesRequired` rather
than returning a value. `Session._run_tool_calls` catches it, asks the user via
`on_escalate_privileges` (if given), and assembles the decision into the tool's result.

The data types and validation logic themselves live in `klorb.tools.escalate_privileges.common`
rather than here, so `klorb.session` can import them without importing this module (which pulls
in `klorb.tools.tool`/`klorb.tools.setup_context`, and so `klorb.session` itself -- see that
module's docstring for the full cycle).
"""

from typing import Any

from klorb.tools.escalate_privileges.common import EscalatePrivilegesRequired, validate_scope
from klorb.tools.tool import Tool


class EscalatePrivilegesTool(Tool):
    """Requests elevated privileges for workspace access in the current session.

    The valid scope values are:
    - "workspace": grants read/write access to the ${workspaceRoot}/.klorb/ directory
      (currently hard-blocked from direct tool access).
    - "homedir": grants read/write access to KLORB_DATA_DIR and KLORB_STATE_DIR
      (the process-wide klorb data and state directories).

    This is a temporary, session-only grant that revokes at the end of the session --
    there is no permanent approval option.

    When invoked, an interstitial approval panel appears explaining the request and
    offering "Approve" and "Deny" options. If approved, the grant takes effect
    immediately for subsequent tool calls in this turn.
    """

    def name(self) -> str:
        return "EscalatePrivileges"

    def description(self) -> str:
        return (
            "Request elevated privileges for workspace access (read/write to ${workspaceRoot}/.klorb/ "
            "or KLORB_DATA_DIR/KLORB_CONFIG_DIR/KLORB_STATE_DIR). "
            "This is a temporary, session-only grant that revokes at the end of the session. "
            "Valid scope values are 'workspace' (access to the workspace's .klorb/ directory) and "
            "'homedir' (access to KLORB_DATA_DIR, KLORB_CONFIG_DIR, and KLORB_STATE_DIR)."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["workspace", "homedir"],
                    "description": (
                        "The scope of the privilege escalation. 'workspace' grants access to the "
                        "workspace's .klorb/ directory. 'homedir' grants access to KLORB_DATA_DIR, "
                        "KLORB_CONFIG_DIR, and KLORB_STATE_DIR."
                    ),
                },
            },
            "required": ["scope"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        scope = args.get("scope")
        if scope is None:
            raise ValueError("Missing required argument 'scope'.")
        validate_scope(scope)
        raise EscalatePrivilegesRequired(scope)

    def summary(self, args: dict[str, Any], result: Any = None, error: str | None = None) -> str:
        if error is not None:
            return f"EscalatePrivileges: {error}"
        scope = args.get("scope", "?")
        return f"EscalatePrivileges({scope})"

    def detail_view(
        self, args: dict[str, Any], result: Any = None, error: str | None = None
    ) -> str:
        if error is not None:
            return f"EscalatePrivileges failed: {error}"
        scope = args.get("scope", "?")
        if result is None:
            return f"EscalatePrivileges({scope}): approval pending"
        return f"EscalatePrivileges({scope}): approved"
