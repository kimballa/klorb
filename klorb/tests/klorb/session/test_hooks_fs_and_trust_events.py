# © Copyright 2026 Aaron Kimball
"""Tests for the `FileSystemModified`/`WorkspaceTrustChanged` event wiring Phase 4 adds:
`Session.fire_session_start_hook` starting (and `close()` stopping) a `FileSystemWatcher`,
`Session._dispatch_fs_modified_event`/`fire_workspace_trust_changed_hook` dispatching an
event's configured actions, and `Session.deliver_event_message`'s queue-vs-fresh-turn/prefix
behavior -- see `klorb.tests.klorb.hooks.test_fs_events` for `FileSystemWatcher`'s own
filesystem-level behavior."""

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from klorb.api_provider import ProviderResponse
from klorb.hooks import fs_events
from klorb.hooks.config import FileSystemModifiedEventConfig, HookConfig, WorkspaceTrustChangedEventConfig
from klorb.hooks.wire import EventInput
from klorb.message import Message
from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, TurnEventHandlers
from klorb.session.events import QueuedMessage
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


@pytest.fixture(autouse=True)
def _hook_env_files_in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the bash handler's hook-env-file directory into `tmp_path` so tests don't
    write to the real KLORB_STATE_DIR (which may be read-only in CI)."""
    monkeypatch.setattr(
        "klorb.hooks.bash_handler._HOOK_ENV_FILES_DIR", tmp_path / "hook-env-files")


def _process_config(workspace_root: Path, **events: Any) -> ProcessConfig:
    session = SessionConfig(
        workspace=Workspace(path=workspace_root, trusted=True),
        read_dirs=DirRules(allow=[workspace_root]),
        write_dirs=DirRules(allow=[workspace_root]))
    return ProcessConfig(session=session, events=events)


def _reply(content: str = "model reply") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=5)


class _FakeWatcher:
    """Stands in for `klorb.hooks.fs_events.FileSystemWatcher`, recording `start()`/`close()`
    without touching a real filesystem or background thread -- `test_fs_events.py` already
    covers the real watcher's own behavior; these tests only need to prove `Session` wires it
    up correctly."""

    def __init__(
        self, workspace_root: Path, entries: list[FileSystemModifiedEventConfig], *,
        dispatch: Any, debounce_seconds: float = 0,
    ) -> None:
        self.workspace_root = workspace_root
        self.entries = entries
        self.dispatch = dispatch
        self.started = False
        self.closed = False


def _install_fake_watcher(monkeypatch: pytest.MonkeyPatch) -> list[_FakeWatcher]:
    created: list[_FakeWatcher] = []

    class _Recording(_FakeWatcher):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

        def start(self) -> None:
            self.started = True

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(fs_events, "FileSystemWatcher", _Recording)
    return created


# --- fire_session_start_hook starting / close() stopping the watcher ---


def test_fire_session_start_hook_starts_the_fs_watcher_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _install_fake_watcher(monkeypatch)
    entry = FileSystemModifiedEventConfig(watch=".", action=HookConfig(type="chat", prompt="x"))
    process_config = _process_config(tmp_path, FileSystemModified=[entry])
    session = Session(process_config.session, process_config=process_config)
    try:
        session.fire_session_start_hook("NewSession")
        assert len(created) == 1
        assert created[0].started is True
        assert created[0].entries == [entry]
        assert created[0].closed is False
    finally:
        session.close()
        assert created[0].closed is True


def test_fire_session_start_hook_does_not_start_a_watcher_with_no_entries_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _install_fake_watcher(monkeypatch)
    process_config = _process_config(tmp_path)
    session = Session(process_config.session, process_config=process_config)
    try:
        session.fire_session_start_hook("NewSession")
        assert created == []
    finally:
        session.close()


def test_fire_session_start_hook_does_not_start_a_watcher_for_a_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _install_fake_watcher(monkeypatch)
    entry = FileSystemModifiedEventConfig(watch=".", action=HookConfig(type="chat", prompt="x"))
    process_config = _process_config(tmp_path, FileSystemModified=[entry])
    root = Session(process_config.session, process_config=process_config)
    child = Session(process_config.session.model_copy(), process_config=process_config, parent=root)
    try:
        child.fire_session_start_hook("NewSession")
        assert created == []
    finally:
        child.close()
        root.close()


# --- _dispatch_fs_modified_event ---


def test_dispatch_fs_modified_event_delivers_the_actions_message(tmp_path: Path) -> None:
    entry = FileSystemModifiedEventConfig(
        watch=".", action=HookConfig(type="bash", shell='echo \'{"message": "fs changed"}\''))
    process_config = _process_config(tmp_path, FileSystemModified=[entry])
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    try:
        session._dispatch_fs_modified_event(
            [entry], EventInput(hook="FileSystemModified", workspaceRoot=str(tmp_path)))
    finally:
        session.close()

    provider.send_prompt.assert_called_once()
    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.content.endswith("An event has resumed this conversation:\nfs changed")


# --- fire_workspace_trust_changed_hook ---


def test_fire_workspace_trust_changed_hook_delivers_the_actions_message(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, WorkspaceTrustChanged=[
        WorkspaceTrustChangedEventConfig(action=HookConfig(type="chat", prompt="trust changed")),
    ])
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    try:
        session.fire_workspace_trust_changed_hook("TrustCommand")
    finally:
        session.close()

    provider.send_prompt.assert_called_once()
    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.content.endswith("An event has resumed this conversation:\ntrust changed")


def test_fire_workspace_trust_changed_hook_is_a_noop_with_no_entries_configured(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path)
    provider = MagicMock()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    try:
        session.fire_workspace_trust_changed_hook("TrustCommand")
    finally:
        session.close()
    provider.send_prompt.assert_not_called()


def test_fire_workspace_trust_changed_hook_is_a_noop_for_a_subagent(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, WorkspaceTrustChanged=[
        WorkspaceTrustChangedEventConfig(action=HookConfig(type="chat", prompt="trust changed")),
    ])
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    root = Session(process_config.session, provider=provider, process_config=process_config)
    child = Session(
        process_config.session.model_copy(), provider=provider, process_config=process_config, parent=root)
    try:
        child.fire_workspace_trust_changed_hook("TrustCommand")
    finally:
        child.close()
        root.close()
    provider.send_prompt.assert_not_called()


# --- deliver_event_message ---


def test_deliver_event_message_starts_a_fresh_prefixed_turn_when_idle(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(SessionConfig(workspace=Workspace(path=tmp_path, trusted=True)), provider=provider)

    session.deliver_event_message("something happened")

    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.content.endswith("An event has resumed this conversation:\nsomething happened")


def test_deliver_event_message_queues_verbatim_when_a_turn_is_in_flight(tmp_path: Path) -> None:
    provider = MagicMock()
    session = Session(SessionConfig(workspace=Workspace(path=tmp_path, trusted=True)), provider=provider)
    session._current_turn_handlers = TurnEventHandlers()

    session.deliver_event_message("something happened")

    provider.send_prompt.assert_not_called()
    drained = session.drain_queued_messages()
    assert drained == [QueuedMessage(message_text="something happened")]
