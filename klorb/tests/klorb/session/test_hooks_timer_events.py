# © Copyright 2026 Aaron Kimball
"""Tests for the `Timer` event wiring Phase 5 adds: `Session.fire_session_start_hook` starting
(and `close()` stopping) a `TimerScheduler`, and `Session._dispatch_timer_event` dispatching an
entry's configured action -- see `klorb.tests.klorb.hooks.test_timer_events` for
`TimerScheduler`'s own scheduling behavior, and `klorb.tests.klorb.session.
test_hooks_fs_and_trust_events` for the equivalent `FileSystemModified`/`WorkspaceTrustChanged`
coverage this mirrors.
"""

from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from klorb.api_provider import ProviderResponse
from klorb.hooks import timer_events
from klorb.hooks.config import HookConfig, TimerEventConfig
from klorb.hooks.wire import EventInput
from klorb.message import Message
from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


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


class _FakeScheduler:
    """Stands in for `klorb.hooks.timer_events.TimerScheduler`, recording `start()`/`close()`
    without touching a real background thread -- `test_timer_events.py` already covers the real
    scheduler's own behavior; these tests only need to prove `Session` wires it up correctly."""

    def __init__(
        self, workspace_root: Path, entries: list[TimerEventConfig], *, dispatch: Any,
    ) -> None:
        self.workspace_root = workspace_root
        self.entries = entries
        self.dispatch = dispatch
        self.started = False
        self.closed = False


def _install_fake_scheduler(monkeypatch: pytest.MonkeyPatch) -> list[_FakeScheduler]:
    created: list[_FakeScheduler] = []

    class _Recording(_FakeScheduler):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            created.append(self)

        def start(self) -> None:
            self.started = True

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(timer_events, "TimerScheduler", _Recording)
    return created


# --- fire_session_start_hook starting / close() stopping the scheduler ---


def test_fire_session_start_hook_starts_the_timer_scheduler_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _install_fake_scheduler(monkeypatch)
    entry = TimerEventConfig(interval_minutes=10, action=HookConfig(type="chat", prompt="tick"))
    process_config = _process_config(tmp_path, Timer=[entry])
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


def test_fire_session_start_hook_does_not_start_a_scheduler_with_no_entries_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _install_fake_scheduler(monkeypatch)
    process_config = _process_config(tmp_path)
    session = Session(process_config.session, process_config=process_config)
    try:
        session.fire_session_start_hook("NewSession")
        assert created == []
    finally:
        session.close()


def test_fire_session_start_hook_does_not_start_a_scheduler_for_a_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _install_fake_scheduler(monkeypatch)
    entry = TimerEventConfig(interval_minutes=10, action=HookConfig(type="chat", prompt="tick"))
    process_config = _process_config(tmp_path, Timer=[entry])
    root = Session(process_config.session, process_config=process_config)
    child = Session(process_config.session.model_copy(), process_config=process_config, parent=root)
    try:
        child.fire_session_start_hook("NewSession")
        assert created == []
    finally:
        child.close()
        root.close()


# --- _dispatch_timer_event ---


def test_dispatch_timer_event_delivers_the_actions_message(tmp_path: Path) -> None:
    entry = TimerEventConfig(
        interval_minutes=10, action=HookConfig(type="bash", shell='echo \'{"message": "tick"}\''))
    process_config = _process_config(tmp_path, Timer=[entry])
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    try:
        session._dispatch_timer_event([entry], EventInput(hook="Timer", workspaceRoot=str(tmp_path)))
    finally:
        session.close()

    provider.send_prompt.assert_called_once()
    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.content.endswith("An event has resumed this conversation:\ntick")
