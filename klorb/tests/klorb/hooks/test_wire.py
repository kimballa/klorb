# © Copyright 2026 Aaron Kimball
from klorb.hooks.wire import EventInput, FileSystemUpdate, HookInput, HookOutput


def test_hook_input_accepts_workspace_root_by_alias() -> None:
    hook_input = HookInput.model_validate({"hook": "onProcessStart", "workspaceRoot": "/ws"})
    assert hook_input.workspace_root == "/ws"
    assert hook_input.name is None
    assert hook_input.args == {}


def test_hook_input_dumps_workspace_root_by_alias() -> None:
    hook_input = HookInput(hook="onProcessStart", workspaceRoot="/ws")
    assert hook_input.model_dump(by_alias=True)["workspaceRoot"] == "/ws"


def test_hook_output_defaults_to_success_with_no_message() -> None:
    output = HookOutput()
    assert output.success is True
    assert output.message is None
    assert output.interrupt is False


def test_hook_input_activate_skill_fields_default_to_none() -> None:
    hook_input = HookInput(hook="onActivateSkill", workspaceRoot="/ws")
    assert hook_input.skill_name is None
    assert hook_input.skill_namespace is None
    assert hook_input.is_user_mentioned is None
    assert hook_input.is_user_activated is None


def test_hook_input_carries_activate_skill_fields() -> None:
    hook_input = HookInput(
        hook="onActivateSkill", workspaceRoot="/ws", skill_name="do-thing",
        skill_namespace="workspace", is_user_mentioned=True, is_user_activated=False)
    assert hook_input.skill_name == "do-thing"
    assert hook_input.skill_namespace == "workspace"
    assert hook_input.is_user_mentioned is True
    assert hook_input.is_user_activated is False


def test_event_input_carries_fs_updates() -> None:
    event_input = EventInput.model_validate({
        "hook": "FileSystemModified",
        "workspaceRoot": "/ws",
        "fs_updates": [{"event": "modified", "path": "src/foo.py"}],
    })
    assert event_input.fs_updates == [FileSystemUpdate(event="modified", path="src/foo.py")]
