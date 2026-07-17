# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.panels.permission_ask_panel.PermissionAskPanel's skill-ask rendering: the
"Activate skill" header, the `/<name> (<namespace>)` preview, and the granted-scope banner."""

from klorb.session import PermissionAskContext
from klorb.tui.panels.permission_ask_panel import PermissionAskPanel, format_ask_context_body


def _skill_ask_ctx() -> PermissionAskContext:
    return PermissionAskContext(
        skill=("workspace", "add-cli-flag"),
        resource_description="activate skill workspace/add-cli-flag: Add a new CLI flag.")


def test_header_text_names_activate_skill_for_a_skill_ask() -> None:
    panel = PermissionAskPanel(_skill_ask_ctx())
    assert panel.header_text() == "Permission requested: Activate skill"


def test_granted_text_names_the_skill_pair() -> None:
    panel = PermissionAskPanel(_skill_ask_ctx(), granted_skill=("workspace", "add-cli-flag"))
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
