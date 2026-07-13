# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.escalate_privileges.escalate_privileges and
klorb.tools.escalate_privileges.common."""

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.escalate_privileges.common import EscalatePrivilegesRequired, validate_scope
from klorb.tools.escalate_privileges.escalate_privileges import EscalatePrivilegesTool
from klorb.tools.setup_context import ToolSetupContext


def _tool() -> EscalatePrivilegesTool:
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=SessionConfig())
    return EscalatePrivilegesTool(context)


def test_validate_scope_accepts_workspace() -> None:
    assert validate_scope("workspace") == "workspace"


@pytest.mark.parametrize("bad_scope", ["", "global", "session", "homedir", "WORKSPACE", "root"])
def test_validate_scope_rejects_anything_but_workspace(bad_scope: str) -> None:
    with pytest.raises(ValueError, match="only valid scope"):
        validate_scope(bad_scope)


def test_apply_raises_for_well_formed_workspace_scope() -> None:
    tool = _tool()

    with pytest.raises(EscalatePrivilegesRequired) as excinfo:
        tool.apply({"scope": "workspace"})

    assert excinfo.value.scope == "workspace"


def test_apply_rejects_missing_scope() -> None:
    tool = _tool()

    with pytest.raises(ValueError, match="Missing required argument 'scope'"):
        tool.apply({})


def test_apply_rejects_invalid_scope() -> None:
    tool = _tool()

    with pytest.raises(ValueError, match="only valid scope"):
        tool.apply({"scope": "homedir"})


def test_parameters_requires_scope_and_forbids_extras() -> None:
    schema = _tool().parameters()
    assert schema["required"] == ["scope"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["scope"]["const"] == "workspace"


def test_summary_names_the_scope() -> None:
    tool = _tool()
    assert tool.summary({"scope": "workspace"}) == "EscalatePrivileges(workspace)"


def test_summary_includes_error_when_present() -> None:
    tool = _tool()
    assert "boom" in tool.summary({"scope": "workspace"}, error="boom")


def test_detail_view_reports_pending_without_result() -> None:
    tool = _tool()
    assert "approval pending" in tool.detail_view({"scope": "workspace"})


def test_detail_view_reports_approved_with_result() -> None:
    tool = _tool()
    assert "approved" in tool.detail_view(
        {"scope": "workspace"}, result={"scope": "workspace", "approved": True})
