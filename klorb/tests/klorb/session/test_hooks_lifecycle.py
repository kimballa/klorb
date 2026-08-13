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


def test_fire_session_start_hook_delivers_a_handlers_log_via_the_notice_handler(
    tmp_path: Path,
) -> None:
    process_config = _process_config(tmp_path, {
        "onSessionStart": [HookConfig(type="bash", shell='echo \'{"log": "debug note"}\'')],
    })
    session = Session(process_config.session, process_config=process_config)
    notices: list[str] = []
    session.register_notice_handler(notices.append)
    try:
        session.fire_session_start_hook("NewSession")
        assert notices == ["debug note"]
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


def test_close_ignores_reset_session_from_onsessionend_and_shuts_down_normally(
    tmp_path: Path,
) -> None:
    """`onSessionEnd`'s result never triggers a reset: a session that's ending never has a live
    host to deliver a continuation to, unlike an `onAgentTurnEnd`-triggered reset, which is
    still on the same call stack as whatever host's `send_turn()` call is waiting on it (see
    docs/specs/hooks-and-events.md's "Session reset" section). `HookDispatcher` drops
    `reset_session` from `onSessionEnd`'s aggregate result before `close()` ever sees it, so
    `reset_session()` never runs and shutdown proceeds as if the hook had returned nothing."""
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
    assert session.scratchpad.path == original_scratchpad_path
    provider.send_prompt.assert_not_called()


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
    assert data["reason"] == "ResumeSession"
    assert data["workspace_trusted"] is True
    assert data["workspace_just_bootstrapped"] is True
    assert data["config"]["prompt_input_max_lines"] == process_config.prompt_input_max_lines


def test_close_does_not_carry_the_resolved_config(tmp_path: Path) -> None:
    output_path = tmp_path / "output.json"
    handler = HookConfig(
        type="bash",
        shell=(
            'python3 -c \'import sys, json; data = json.load(sys.stdin); '
            f'open("{output_path}", "w").write(json.dumps(data))\'; '
            'echo \'{}\''))
    process_config = _process_config(tmp_path, {"onSessionEnd": [handler]})
    session = Session(process_config.session, process_config=process_config)
    session.close()
    data = json.loads(output_path.read_text())
    assert data["hook"] == "onSessionEnd"
    assert data["config"] is None
