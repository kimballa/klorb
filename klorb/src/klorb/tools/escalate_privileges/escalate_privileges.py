# © Copyright 2026 Aaron Kimball
"""A Tool that requests elevated privileges for workspace access."""

from typing import Any

from klorb.tools.escalate_privileges.common import EscalatePrivilegesRequired, validate_scope
from klorb.tools.tool import Tool


class EscalatePrivilegesTool(Tool):
    """Requests elevated privileges for workspace access in the current session.

    The valid scope values are:
    - "workspace": grants read/write access to the ${workspaceRoot}/.klorb/ directory.
    - "homedir": grants read/write access to KLORB_DATA_DIR and KLORB_STATE_DIR.

    This is a temporary, session-only grant that revokes at the end of the session.

    When invoked, an interstitial approval panel appears explaining the request and
    offering "Approve" and "Deny" options. If approved, the grant takes effect
    immediately for subsequent tool calls in this turn.
    """

    def name(self) -> str:
        return "EscalatePrivileges"

    def category(self) -> str:
        return "PRIVILEGES"

    def is_read_only(self) -> bool:
        return False

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
                "reason": {
                    "type": "string",
                    "description": (
                        "A short explanation of why this escalation is needed. Shown to the user "
                        "in the approval panel."
                    ),
                },
            },
            "required": ["scope", "reason"],
            "additionalProperties": False,
        }

    def apply(self, args: dict[str, Any]) -> Any:
        scope = args.get("scope")
        if scope is None:
            raise ValueError("Missing required argument 'scope'.")
        reason = args.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Missing required argument 'reason'.")
        validate_scope(scope)
        raise EscalatePrivilegesRequired(scope, reason)

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
