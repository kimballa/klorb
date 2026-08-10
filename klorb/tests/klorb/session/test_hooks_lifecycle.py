# © Copyright 2026 Aaron Kimball
"""Tests for `Session.fire_session_start_hook`/`close()` dispatching `onSessionStart`/
`onSessionEnd` -- root session only; a subagent never fires either.
"""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.api_provider import ProviderResponse
from klorb.hooks.config import HookConfig
from klorb.message import Message
from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.workspace import Workspace


def _reply(content: str = "model reply") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=5)


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


@pytest.fixture(autouse=True)
def _hook_env_files_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the bash handler's hook-env-file directory into `tmp_path` so tests don't
    write to the real KLORB_STATE_DIR (which may be read-only in CI)."""
    monkeypatch.setattr(
        "klorb.hooks.bash_handler._HOOK_ENV_FILES_DIR", tmp_path / "hook-env-files")


def _process_config(workspace_root: Path, hooks: dict[str, list[HookConfig]]) -> ProcessConfig:
    session = SessionConfig(
        workspace=Workspace(path=workspace_root, trusted=True),
        read_dirs=DirRules(allow=[workspace_root]),
        write_dirs=DirRules(allow=[workspace_root]))
    return ProcessConfig(session=session, hooks=hooks)


def _marker_handler(marker: Path) -> HookConfig:
    return HookConfig(type="bash", shell=f'touch "{marker}"; echo \'{{"message": "ran"}}\'')


def test_fire_session_start_hook_runs_onsessionstart_for_a_root_session(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    process_config = _process_config(tmp_path, {"onSessionStart": [_marker_handler(marker)]})
    session = Session(process_config.session, process_config=process_config)
    try:
        session.fire_session_start_hook("NewSession")
        assert marker.exists()
    finally:
        session.close()


def test_fire_session_start_hook_is_a_noop_for_a_subagent(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    process_config = _process_config(tmp_path, {"onSessionStart": [_marker_handler(marker)]})
    root = Session(process_config.session, process_config=process_config)
    child = Session(process_config.session.model_copy(), process_config=process_config, parent=root)
    try:
        child.fire_session_start_hook("NewSession")
        assert not marker.exists()
    finally:
        child.close()
        root.close()


def test_fire_session_start_hook_is_a_noop_without_process_config(tmp_path: Path) -> None:
    session = Session(SessionConfig(workspace=Workspace(path=tmp_path, trusted=True)))
    try:
        session.fire_session_start_hook("NewSession")
    finally:
        session.close()


def test_close_runs_onsessionend_for_a_root_session(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    process_config = _process_config(tmp_path, {"onSessionEnd": [_marker_handler(marker)]})
    session = Session(process_config.session, process_config=process_config)
    session.close()
    assert marker.exists()


def test_close_does_not_run_onsessionend_for_a_subagent(tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    process_config = _process_config(tmp_path, {"onSessionEnd": [_marker_handler(marker)]})
    root = Session(process_config.session, process_config=process_config)
    child = Session(process_config.session.model_copy(), process_config=process_config, parent=root)
    child.close()
    assert not marker.exists()
    root.close()
    assert marker.exists()


def test_close_with_reset_session_resets_in_place_instead_of_closing(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onSessionEnd": [
            HookConfig(type="bash", shell='echo \'{"message": "restart me", "reset_session": true}\''),
        ],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    original_id = session.id
    original_scratchpad_path = session.scratchpad.path

    session.close()

    assert session.id == original_id
    assert session.scratchpad.path != original_scratchpad_path
    user_messages = [m.content for m in session.messages if m.role == "user"]
    assert len(user_messages) == 1
    assert user_messages[0].endswith("restart me")


def test_fire_session_start_hook_carries_workspace_trust_fields(tmp_path: Path) -> None:
    output_path = tmp_path / "output.json"
    handler = HookConfig(
        type="bash",
        shell=(
            'python3 -c \'import sys, json; data = json.load(sys.stdin); '
            f'open("{output_path}", "w").write(json.dumps(data))\'; '
            'echo \'{}\''))
    process_config = _process_config(tmp_path, {"onSessionStart": [handler]})
    session = Session(process_config.session, process_config=process_config)
    try:
        session.fire_session_start_hook("ResumeSession", workspace_just_bootstrapped=True)
    finally:
        session.close()
    data = json.loads(output_path.read_text())
    assert data["hook"] == "onSessionStart"
    assert data["event"] == "ResumeSession"
    assert data["workspaceTrusted"] is True
    assert data["workspaceJustBootstrapped"] is True
