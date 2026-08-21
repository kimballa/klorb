# © Copyright 2026 Aaron Kimball
"""Tests for the `FileSystemModified`/`WorkspaceTrustChanged` event wiring Phase 4 adds:
`Session.fire_session_start_hook` starting (and `close()` stopping) a `FileSystemWatcher`,
`Session._dispatch_fs_modified_event`/`fire_workspace_trust_changed_hook` dispatching an
event's configured actions (including a `reset_session` result, via `_deliver_or_reset_event`),
and `Session.deliver_event_message`'s queue-vs-wake-vs-raise behavior (a live turn to queue
behind, a registered wake handler to enqueue-and-wake, or
`ChainedHookMessageUndeliverableError` with neither -- see
docs/adrs/00187-session-register-wake-handler-tells-an-idle-host-to-drain-and-resubmit.md)
-- see `klorb.tests.klorb.hooks.test_fs_events` for `FileSystemWatcher`'s own filesystem-level
behavior."""
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from klorb.api_provider import ProviderResponse
from klorb.hooks import fs_events
from klorb.hooks.config import FileSystemModifiedEventConfig, HookConfig, WorkspaceTrustChangedEventConfig
from klorb.hooks.hook_api import EventInput
from klorb.message import Message
from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, TurnEventHandlers
from klorb.session.constants import ChainedHookMessageUndeliverableError
from klorb.session.events import QueuedMessage
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


def _process_config(
    workspace_root: Path, make_session_config: Callable[..., SessionConfig], **events: Any,
) -> ProcessConfig:
    session = make_session_config(
        workspace=Workspace(path=workspace_root, trusted=True),
        read_dirs=DirRules(allow=[workspace_root]),
        write_dirs=DirRules(allow=[workspace_root]),
        events=events)
    return ProcessConfig(session=session)


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    created = _install_fake_watcher(monkeypatch)
    entry = FileSystemModifiedEventConfig(watch=".", action=HookConfig(type="chat", prompt="x"))
    process_config = _process_config(tmp_path, make_session_config, FileSystemModified=[entry])
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    created = _install_fake_watcher(monkeypatch)
    process_config = _process_config(tmp_path, make_session_config)
    session = Session(process_config.session, process_config=process_config)
    try:
        session.fire_session_start_hook("NewSession")
        assert created == []
    finally:
        session.close()


def test_fire_session_start_hook_does_not_start_a_watcher_for_a_subagent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    created = _install_fake_watcher(monkeypatch)
    entry = FileSystemModifiedEventConfig(watch=".", action=HookConfig(type="chat", prompt="x"))
    process_config = _process_config(tmp_path, make_session_config, FileSystemModified=[entry])
    root = Session(process_config.session, process_config=process_config)
    child = Session(process_config.session.model_copy(), process_config=process_config, parent=root)
    try:
        child.fire_session_start_hook("NewSession")
        assert created == []
    finally:
        child.close()
        root.close()


# --- _dispatch_fs_modified_event ---


def test_dispatch_fs_modified_event_delivers_the_actions_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """Delivered via `deliver_event_message`'s "a turn is already in flight" branch -- the only
    one that can render anywhere without a live host (see "Available events" in
    docs/specs/hooks-and-events.md); the idle branch raises instead, covered separately by
    `test_deliver_event_message_raises_when_idle`."""
    entry = FileSystemModifiedEventConfig(
        watch=".", action=HookConfig(type="bash", shell='echo \'{"message": "fs changed"}\''))
    process_config = _process_config(tmp_path, make_session_config, FileSystemModified=[entry])
    provider = MagicMock()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    session._current_turn_handlers = TurnEventHandlers()
    try:
        session._dispatch_fs_modified_event(
            [entry], EventInput(hook="FileSystemModified", workspace_root=str(tmp_path)))
    finally:
        session._current_turn_handlers = None
        session.close()

    provider.send_prompt.assert_not_called()
    drained = session.drain_queued_messages()
    assert drained == [QueuedMessage(message_text="fs changed", origin="event")]


# --- fire_workspace_trust_changed_hook ---


def test_fire_workspace_trust_changed_hook_delivers_the_actions_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, WorkspaceTrustChanged=[
        WorkspaceTrustChangedEventConfig(action=HookConfig(type="chat", prompt="trust changed")),
    ])
    provider = MagicMock()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    session._current_turn_handlers = TurnEventHandlers()
    try:
        session.fire_workspace_trust_changed_hook("TrustCommand")
    finally:
        session._current_turn_handlers = None
        session.close()

    provider.send_prompt.assert_not_called()
    drained = session.drain_queued_messages()
    assert drained == [QueuedMessage(message_text="trust changed", origin="event")]


def test_fire_workspace_trust_changed_hook_is_a_noop_with_no_entries_configured(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config)
    provider = MagicMock()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    try:
        session.fire_workspace_trust_changed_hook("TrustCommand")
    finally:
        session.close()
    provider.send_prompt.assert_not_called()


def test_fire_workspace_trust_changed_hook_is_a_noop_for_a_subagent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, WorkspaceTrustChanged=[
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


def test_fire_workspace_trust_changed_hook_reset_session_resets_and_wakes_the_host(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A `reset_session` result wipes the conversation in place (same `id`) and delivers its
    `message` via `deliver_event_message` like an ordinary event message -- which, while idle,
    means enqueuing and pinging the registered wake handler (see `Session._deliver_or_reset_event`)."""
    process_config = _process_config(tmp_path, make_session_config, WorkspaceTrustChanged=[
        WorkspaceTrustChangedEventConfig(
            action=HookConfig(
                type="bash",
                shell='echo \'{"message": "fresh start", "reset_session": true}\'')),
    ])
    provider = MagicMock()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    session.append_system_note("stale conversation")
    original_id = session.id
    woken: list[bool] = []
    session.register_wake_handler(lambda: woken.append(True))

    try:
        session.fire_workspace_trust_changed_hook("TrustCommand")
    finally:
        session.close()

    assert session.id == original_id
    assert session.messages == []
    assert woken == [True]
    provider.send_prompt.assert_not_called()
    drained = session.drain_queued_messages()
    assert drained == [QueuedMessage(message_text="fresh start", origin="event")]


# --- deliver_event_message ---


def test_deliver_event_message_raises_when_idle(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """No turn in flight and no registered wake handler means no host (TUI/ACP/headless) is
    around to render the message anywhere -- raises `ChainedHookMessageUndeliverableError`
    rather than dispatching it invisibly."""
    provider = MagicMock()
    session = Session(make_session_config(workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider)

    with pytest.raises(ChainedHookMessageUndeliverableError):
        session.deliver_event_message("something happened")

    provider.send_prompt.assert_not_called()


def test_deliver_event_message_wakes_a_registered_host_when_idle(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A registered wake handler (see `Session.register_wake_handler`) lets an idle session
    enqueue instead of raising -- the "push-and-wake-up" mechanism the no-handler case above
    still falls back to raising without."""
    provider = MagicMock()
    session = Session(make_session_config(workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider)
    woken: list[bool] = []
    session.register_wake_handler(lambda: woken.append(True))

    session.deliver_event_message("something happened")

    assert woken == [True]
    provider.send_prompt.assert_not_called()
    drained = session.drain_queued_messages()
    assert drained == [QueuedMessage(message_text="something happened", origin="event")]


def test_deliver_event_message_queues_verbatim_when_a_turn_is_in_flight(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    provider = MagicMock()
    session = Session(make_session_config(workspace=Workspace(path=tmp_path, trusted=True)),
        provider=provider)
    session._current_turn_handlers = TurnEventHandlers()

    session.deliver_event_message("something happened")

    provider.send_prompt.assert_not_called()
    drained = session.drain_queued_messages()
    assert drained == [QueuedMessage(message_text="something happened", origin="event")]
