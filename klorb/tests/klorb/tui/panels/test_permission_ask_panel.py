# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.panels.permission_ask_panel.PermissionAskPanel's skill-ask rendering: the
"Activate skill" header, the `/<name> (<namespace>)` preview, and the granted-scope banner --
plus a regression test that `action_confirm` reports back the same command pattern the granted-
scope banner names."""

from klorb.permissions.resource import BashCommandContext, CommandResource, GrantPreview, SkillResource
from klorb.session import PermissionAskContext, PermissionDecision
from klorb.tui.panels.permission_ask_panel import PermissionAskPanel, format_ask_context_body


def _command_ask_ctx() -> PermissionAskContext:
    return PermissionAskContext(
        resource=CommandResource(argv=("grep", "-rn", "TODO", "src/foo.py")),
        bash_context=BashCommandContext(command_text="grep -rn TODO src/foo.py"),
        resource_description="run command: grep -rn TODO src/foo.py")


def test_action_confirm_reports_the_granted_command_pattern_on_the_decision() -> None:
    """Regression test: a persistent Allow must carry the exact pattern the panel's own
    "grants: ..." copy named -- e.g. a risk-classifier-suggested wildcard -- so
    `Session._retry_after_multi_permission_decisions` persists that pattern instead of
    recomputing a possibly-different one from the item's raw argv."""
    decisions: list[PermissionDecision] = []
    panel = PermissionAskPanel(
        _command_ask_ctx(), grant_patterns=[["grep", "**"]],
        on_dismiss=decisions.append)

    panel.action_confirm()

    assert len(decisions) == 1
    assert decisions[0].action == "allow"
    assert decisions[0].grant_patterns == [["grep", "**"]]


def _skill_ask_ctx() -> PermissionAskContext:
    return PermissionAskContext(
        resource=SkillResource(skill_id=("workspace", "add-cli-flag")),
        resource_description="activate skill workspace/add-cli-flag: Add a new CLI flag.")


def test_header_text_names_activate_skill_for_a_skill_ask() -> None:
    panel = PermissionAskPanel(_skill_ask_ctx())
    assert panel.header_text() == "Permission requested: Activate skill"


def test_granted_text_names_the_skill_pair() -> None:
    panel = PermissionAskPanel(
        _skill_ask_ctx(), granted_preview=GrantPreview(resource_text="/add-cli-flag (workspace)"))
    granted = panel._granted_text()
    assert granted is not None
    assert "/add-cli-flag (workspace)" in str(granted)


def test_granted_text_is_none_without_a_granted_skill() -> None:
    panel = PermissionAskPanel(_skill_ask_ctx())
    assert panel._granted_text() is None


def test_format_ask_context_body_includes_skill_preview_line() -> None:
    body = format_ask_context_body(_skill_ask_ctx())
    lines = body.splitlines()
    assert lines[0] == "/add-cli-flag (workspace)"
    assert lines[1] == "activate skill workspace/add-cli-flag: Add a new CLI flag."
