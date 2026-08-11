# © Copyright 2026 Aaron Kimball
from klorb.hooks.hook_api import EventInput, FileSystemUpdate, HookInput, HookOutput


def test_hook_input_accepts_workspace_root() -> None:
    hook_input = HookInput.model_validate({"hook": "onProcessStart", "workspace_root": "/ws"})
    assert hook_input.workspace_root == "/ws"
    assert hook_input.name is None
    assert hook_input.args == {}


def test_hook_input_dumps_workspace_root() -> None:
    hook_input = HookInput(hook="onProcessStart", workspace_root="/ws")
    assert hook_input.model_dump()["workspace_root"] == "/ws"


def test_hook_output_defaults_to_success_with_no_message() -> None:
    output = HookOutput()
    assert output.success is True
    assert output.message is None
    assert output.interrupt is False


def test_hook_input_activate_skill_fields_default_to_none() -> None:
    hook_input = HookInput(hook="onActivateSkill", workspace_root="/ws")
    assert hook_input.skill_name is None
    assert hook_input.skill_namespace is None
    assert hook_input.is_user_mentioned is None
    assert hook_input.is_user_activated is None


def test_hook_input_carries_activate_skill_fields() -> None:
    hook_input = HookInput(
        hook="onActivateSkill", workspace_root="/ws", skill_name="do-thing",
        skill_namespace="workspace", is_user_mentioned=True, is_user_activated=False)
    assert hook_input.skill_name == "do-thing"
    assert hook_input.skill_namespace == "workspace"
    assert hook_input.is_user_mentioned is True
    assert hook_input.is_user_activated is False


def test_event_input_carries_fs_updates() -> None:
    event_input = EventInput.model_validate({
        "hook": "FileSystemModified",
        "workspace_root": "/ws",
        "fs_updates": [{"event": "modified", "path": "src/foo.py"}],
    })
    assert event_input.fs_updates == [FileSystemUpdate(event="modified", path="src/foo.py")]


def test_event_input_is_agent_active_defaults_to_none() -> None:
    event_input = EventInput(hook="Timer", workspace_root="/ws")
    assert event_input.is_agent_active is None


def test_event_input_carries_is_agent_active() -> None:
    event_input = EventInput(hook="Timer", workspace_root="/ws", is_agent_active=True)
    assert event_input.is_agent_active is True
