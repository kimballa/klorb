# © Copyright 2026 Aaron Kimball
"""Tests for workspace context-file injection
(`Session._build_context_files_interjection`/`Session.send_turn`)."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from klorb.api_provider import ProviderResponse
from klorb.message import Message
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.workspace import Workspace


def _session(
    workspace_root: Path,
    *,
    trusted: bool = True,
    compatibility_claude_markdown: bool = False,
    provider: MagicMock | None = None,
) -> Session:
    """Build a `Session` with a mock provider whose workspace_root is `workspace_root`."""
    config = SessionConfig(
        model="some/model", workspace=Workspace(path=workspace_root, trusted=trusted))
    return Session(
        config,
        provider=provider if provider is not None else MagicMock(),
        process_config=ProcessConfig(compatibility_claude_markdown=compatibility_claude_markdown),
    )


def _user_messages(session: Session) -> list[Message]:
    """Return only the `role="user"` messages in `session`'s history."""
    return [m for m in session.messages if m.role == "user"]


def _reply(content: str = "ok") -> ProviderResponse:
    """A plain, non-tool-calling provider reply for `send_turn()` to run its one round trip
    against, without touching a real API."""
    return ProviderResponse(
        message=Message(
            content=content,
            role="assistant",
            num_tokens=0,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason="stop",
        ),
        prompt_tokens=0,
    )


def test_no_interjection_when_no_files_exist(tmp_path: Path) -> None:
    session = _session(tmp_path)
    assert session._build_context_files_interjection() is None


def test_no_interjection_when_workspace_untrusted(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Be careful with tests.")
    (tmp_path / ".klorb").mkdir()
    (tmp_path / ".klorb" / "INSTRUCTIONS.md").write_text("Durable per-project instructions.")

    session = _session(tmp_path, trusted=False)
    assert session._build_context_files_interjection() is None


def test_untrusted_workspace_never_touches_filesystem(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("content")

    session = _session(tmp_path, trusted=False)
    session.config.workspace.path = Path("/nonexistent/does/not/exist")
    # Would raise if the untrusted path were ever resolved/read.
    assert session._build_context_files_interjection() is None


def test_agents_md_wrapped_in_context_file_tag(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Be careful with tests.")

    session = _session(tmp_path)
    body = session._build_context_files_interjection()

    assert body is not None
    assert "Be careful with tests." in body
    assert '<ContextFile filename="AGENTS.md" priority="1">' in body


def test_klorb_instructions_md_wrapped_in_context_file_tag(tmp_path: Path) -> None:
    (tmp_path / ".klorb").mkdir()
    (tmp_path / ".klorb" / "INSTRUCTIONS.md").write_text("Durable per-project instructions.")

    session = _session(tmp_path)
    body = session._build_context_files_interjection()

    assert body is not None
    assert "Durable per-project instructions." in body
    assert '<ContextFile filename=".klorb/INSTRUCTIONS.md" priority="1">' in body


def test_instructions_take_priority_one_agents_priority_two(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agents content")
    (tmp_path / ".klorb").mkdir()
    (tmp_path / ".klorb" / "INSTRUCTIONS.md").write_text("instructions content")

    session = _session(tmp_path)
    body = session._build_context_files_interjection()

    assert body is not None
    assert '<ContextFile filename=".klorb/INSTRUCTIONS.md" priority="1">' in body
    assert '<ContextFile filename="AGENTS.md" priority="2">' in body
    assert body.index(".klorb/INSTRUCTIONS.md") < body.index("AGENTS.md")


def test_claude_md_not_read_by_default(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agents content")
    (tmp_path / "CLAUDE.md").write_text("claude content")

    session = _session(tmp_path)
    body = session._build_context_files_interjection()

    assert body is not None
    assert "agents content" in body
    assert "claude content" not in body


def test_claude_md_read_when_compatibility_enabled(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agents content")
    (tmp_path / "CLAUDE.md").write_text("claude content")

    session = _session(tmp_path, compatibility_claude_markdown=True)
    body = session._build_context_files_interjection()

    assert body is not None
    assert "agents content" in body
    assert "claude content" in body
    assert '<ContextFile filename="AGENTS.md" priority="1">' in body
    assert '<ContextFile filename="CLAUDE.md" priority="2">' in body


def test_claude_md_compat_enabled_but_untrusted(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agents content")
    (tmp_path / "CLAUDE.md").write_text("claude content")

    session = _session(tmp_path, trusted=False, compatibility_claude_markdown=True)
    assert session._build_context_files_interjection() is None


def test_context_files_framed_as_context_not_task(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("project rules")

    session = _session(tmp_path)
    body = session._build_context_files_interjection()

    assert body is not None
    assert "standing guidance" in body
    assert "not a task to act on directly" in body


def test_applicable_filenames_default() -> None:
    session = _session(Path("/tmp"))
    assert session._applicable_context_filenames() == [".klorb/INSTRUCTIONS.md", "AGENTS.md"]


def test_applicable_filenames_with_compat() -> None:
    session = _session(Path("/tmp"), compatibility_claude_markdown=True)
    assert session._applicable_context_filenames() == [
        ".klorb/INSTRUCTIONS.md", "AGENTS.md", "CLAUDE.md",
    ]


def test_send_turn_prepends_project_guidance_interjection(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Be careful with tests.")

    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=mock_provider)
    session.send_turn("do the task")

    user_msgs = _user_messages(session)
    assert len(user_msgs) == 1
    assert '<SystemInterjection subject="ProjectGuidance">' in user_msgs[0].content
    assert "Be careful with tests." in user_msgs[0].content
    assert "do the task" in user_msgs[0].content
    assert session._context_files_seeded is True


def test_send_turn_no_context_file_interjection_when_untrusted(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Be careful with tests.")

    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, trusted=False, provider=mock_provider)
    session.send_turn("do the task")

    user_msgs = _user_messages(session)
    assert len(user_msgs) == 1
    # The workspace-trust gate keeps the untrusted workspace's AGENTS.md out of the prompt: no
    # ProjectGuidance interjection, and none of the file's content. This module's autouse
    # `_neutralize_packaged_skills` fixture stubs skill discovery to always return `[]`, so no
    # AvailableSkills interjection can ride here either -- the prompt is untouched.
    assert '<SystemInterjection subject="ProjectGuidance">' not in user_msgs[0].content
    assert "Be careful with tests." not in user_msgs[0].content
    assert user_msgs[0].content == "do the task"
    assert session._context_files_seeded is True


def test_send_turn_only_prepends_interjection_once(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Be careful with tests.")

    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=mock_provider)
    session.send_turn("first")
    session.send_turn("second")

    user_msgs = _user_messages(session)
    assert len(user_msgs) == 2
    assert "ProjectGuidance" in user_msgs[0].content
    assert "ProjectGuidance" not in user_msgs[1].content
