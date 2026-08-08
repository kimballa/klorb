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


def test_event_input_carries_fs_updates() -> None:
    event_input = EventInput.model_validate({
        "hook": "FileSystemModified",
        "workspaceRoot": "/ws",
        "fs_updates": [{"event": "modified", "path": "src/foo.py"}],
    })
    assert event_input.fs_updates == [FileSystemUpdate(event="modified", path="src/foo.py")]
