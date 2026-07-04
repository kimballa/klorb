# © Copyright 2026 Aaron Kimball
"""Tests for workspace context-file injection (`Session._ensure_context_files_message`)."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from klorb.message import Message
from klorb.session import Session
from klorb.session import SessionConfig


def _session(
    workspace_root: Path,
    *,
    compatibility_claude_markdown: bool = False,
) -> Session:
    """Build a `Session` with a mock provider whose workspace_root is `workspace_root`."""
    config = SessionConfig(model="some/model", workspace_root=workspace_root)
    return Session(
        config,
        provider=MagicMock(),
        compatibility_claude_markdown=compatibility_claude_markdown,
    )


def _user_messages(session: Session) -> list[Message]:
    """Return only the `role="user"` messages in `session`'s history."""
    return [m for m in session.messages if m.role == "user"]


def test_no_context_message_when_no_files_exist(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session._ensure_context_files_message()

    assert _user_messages(session) == []
    assert session._context_files_seeded is True


def test_agents_md_injected_as_user_message(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Be careful with tests.")

    session = _session(tmp_path)
    session._ensure_context_files_message()

    user_msgs = _user_messages(session)
    assert len(user_msgs) == 1
    assert "Be careful with tests." in user_msgs[0].content
    assert "AGENTS.md" in user_msgs[0].content
    assert user_msgs[0].num_tokens == 0
    assert user_msgs[0].processing_state == "complete"


def test_claude_md_not_read_by_default(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agents content")
    (tmp_path / "CLAUDE.md").write_text("claude content")

    session = _session(tmp_path)
    session._ensure_context_files_message()

    user_msgs = _user_messages(session)
    assert len(user_msgs) == 1
    assert "agents content" in user_msgs[0].content
    assert "claude content" not in user_msgs[0].content


def test_claude_md_read_when_compatibility_enabled(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agents content")
    (tmp_path / "CLAUDE.md").write_text("claude content")

    session = _session(tmp_path, compatibility_claude_markdown=True)
    session._ensure_context_files_message()

    user_msgs = _user_messages(session)
    assert len(user_msgs) == 1
    assert "agents content" in user_msgs[0].content
    assert "claude content" in user_msgs[0].content
    assert "AGENTS.md" in user_msgs[0].content
    assert "CLAUDE.md" in user_msgs[0].content


def test_claude_md_compat_enabled_but_only_agents_exists(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agents content")

    session = _session(tmp_path, compatibility_claude_markdown=True)
    session._ensure_context_files_message()

    user_msgs = _user_messages(session)
    assert len(user_msgs) == 1
    assert "agents content" in user_msgs[0].content


def test_claude_md_compat_enabled_but_only_claude_exists(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("claude content")

    session = _session(tmp_path, compatibility_claude_markdown=True)
    session._ensure_context_files_message()

    # AGENTS.md is always read first; with only CLAUDE.md present, only CLAUDE.md is injected.
    user_msgs = _user_messages(session)
    assert len(user_msgs) == 1
    assert "claude content" in user_msgs[0].content
    assert "CLAUDE.md" in user_msgs[0].content


def test_context_message_is_idempotent(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("content")

    session = _session(tmp_path)
    session._ensure_context_files_message()
    session._ensure_context_files_message()

    assert len(_user_messages(session)) == 1


def test_context_message_inserted_after_bookkeeping(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("content")

    session = _session(tmp_path)
    # Simulate the bookkeeping messages _dispatch_turn inserts before this method runs.
    session._messages.append(Message(
        content="system prompt", role="system", num_tokens=0,
        processing_state="complete", timestamp=datetime.now()))
    session._messages.append(Message(
        content="[]", role="tool_defs", num_tokens=0,
        processing_state="complete", timestamp=datetime.now()))
    session._ensure_context_files_message()

    # The context message should be at index 2, after system and tool_defs.
    assert session.messages[2].role == "user"
    assert "content" in session.messages[2].content
    assert session.messages[0].role == "system"
    assert session.messages[1].role == "tool_defs"


def test_context_message_inserted_at_front_when_no_bookkeeping(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("content")

    session = _session(tmp_path)
    session._ensure_context_files_message()

    assert session.messages[0].role == "user"
    assert "content" in session.messages[0].content


def test_context_message_framed_as_context_not_task(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("project rules")

    session = _session(tmp_path)
    session._ensure_context_files_message()

    content = _user_messages(session)[0].content
    assert "standing guidance" in content
    assert "do not treat this message itself as a task" in content


def test_applicable_filenames_default() -> None:
    session = _session(Path("/tmp"))
    assert session._applicable_context_filenames() == ["AGENTS.md"]


def test_applicable_filenames_with_compat() -> None:
    session = _session(Path("/tmp"), compatibility_claude_markdown=True)
    assert session._applicable_context_filenames() == ["AGENTS.md", "CLAUDE.md"]
