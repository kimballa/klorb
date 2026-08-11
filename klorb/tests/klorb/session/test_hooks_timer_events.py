# © Copyright 2026 Aaron Kimball
"""Tests for the `Timer` event wiring Phase 5 adds: `Session.fire_session_start_hook` starting
(and `close()` stopping) a `TimerScheduler`, and `Session._dispatch_timer_event` dispatching an
entry's configured action -- see `klorb.tests.klorb.hooks.test_timer_events` for
`TimerScheduler`'s own scheduling behavior, and `klorb.tests.klorb.session.
test_hooks_fs_and_trust_events` for the equivalent `FileSystemModified`/`WorkspaceTrustChanged`
coverage this mirrors.
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from klorb.hooks import timer_events
from klorb.hooks.config import HookConfig, TimerEventConfig
from klorb.hooks.hook_api import EventInput
from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, TurnEventHandlers
from klorb.session.events import QueuedMessage
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
    """Delivered via `deliver_event_message`'s "a turn is already in flight" branch -- the only
    one that can render anywhere without a live host (see "Available events" in
    docs/specs/hooks-and-events.md); the idle branch raises `ChainedHookMessageUndeliverableError`
    instead, covered by `klorb.tests.klorb.session.test_hooks_fs_and_trust_events.
    test_deliver_event_message_raises_when_idle`."""
    entry = TimerEventConfig(
        interval_minutes=10, action=HookConfig(type="bash", shell='echo \'{"message": "tick"}\''))
    process_config = _process_config(tmp_path, Timer=[entry])
    provider = MagicMock()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    session._current_turn_handlers = TurnEventHandlers()
    try:
        session._dispatch_timer_event([entry], EventInput(hook="Timer", workspace_root=str(tmp_path)))
    finally:
        session._current_turn_handlers = None
        session.close()

    provider.send_prompt.assert_not_called()
    drained = session.drain_queued_messages()
    assert drained == [QueuedMessage(message_text="tick", origin="event")]


def test_dispatch_timer_event_reset_session_resets_and_wakes_the_host(tmp_path: Path) -> None:
    """A `reset_session` result wipes the conversation in place (same `id`) and delivers its
    `message` via `deliver_event_message` like an ordinary event message -- which, while idle,
    means enqueuing and pinging the registered wake handler (see `Session._deliver_or_reset_event`,
    shared with `klorb.tests.klorb.session.test_hooks_fs_and_trust_events`'s
    `WorkspaceTrustChanged` coverage of the same branch)."""
    entry = TimerEventConfig(
        interval_minutes=10,
        action=HookConfig(
            type="bash", shell='echo \'{"message": "fresh start", "reset_session": true}\''))
    process_config = _process_config(tmp_path, Timer=[entry])
    provider = MagicMock()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    session.append_system_note("stale conversation")
    original_id = session.id
    woken: list[bool] = []
    session.register_wake_handler(lambda: woken.append(True))

    try:
        session._dispatch_timer_event([entry], EventInput(hook="Timer", workspace_root=str(tmp_path)))
    finally:
        session.close()

    assert session.id == original_id
    assert session.messages == []
    assert woken == [True]
    provider.send_prompt.assert_not_called()
    drained = session.drain_queued_messages()
    assert drained == [QueuedMessage(message_text="fresh start", origin="event")]
