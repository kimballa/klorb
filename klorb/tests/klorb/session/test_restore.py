# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session.restore -- try_restore_session's rehydration of path-backed image
fragments (see docs/adrs/00169-store-image-fragments-on-disk-not-inline-in-session-json.md)."""

import base64
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.message import Message, MessageFragment
from klorb.models.registry import ModelRegistry
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.session.restore import try_restore_session
from klorb.workspace import Workspace
from klorb.workspace import input_history as input_history_module
from klorb.workspace.session_store import RecentSession, write_session_image, write_session_state


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(input_history_module, "get_klorb_data_dir", lambda: tmp_path / "data")


def _workspace(tmp_path: Path) -> Workspace:
    return Workspace(id="abcd-1234", path=tmp_path / "foobar", is_project=True, trusted=True)


def test_restore_rehydrates_a_path_backed_image_fragment(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    raw_bytes = b"not-really-a-png-but-bytes-are-bytes"
    image_path = write_session_image(workspace, "sess-1", raw_bytes, "webp")
    message = Message(
        content="look", role="user", num_tokens=3, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 0),
        fragments=[
            MessageFragment(type="text", text="look"),
            MessageFragment(
                type="image_url", image_url=None, image_path=image_path, mime_type="image/webp"),
        ])
    write_session_state(workspace, "sess-1", SessionConfig(workspace=workspace), [message])

    session = try_restore_session(
        workspace, RecentSession(session_id="sess-1", subdir="sess-1"),
        provider=MagicMock(), model_registry=ModelRegistry(), process_config=ProcessConfig())

    assert session is not None
    restored_fragments = session.messages[0].fragments
    assert restored_fragments is not None
    image_fragment = restored_fragments[1]
    assert image_fragment.image_path == image_path
    assert image_fragment.image_url is not None
    expected_b64 = base64.b64encode(raw_bytes).decode("ascii")
    assert image_fragment.image_url["url"] == f"data:image/webp;base64,{expected_b64}"
    # provider_content() needs no filesystem access once rehydrated.
    assert session.messages[0].provider_content() == [
        {"type": "text", "text": "look"},
        {"type": "image_url", "image_url": {"url": f"data:image/webp;base64,{expected_b64}"}},
    ]
    session.close()
