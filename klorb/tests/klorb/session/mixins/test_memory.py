# © Copyright 2026 Aaron Kimball
"""Tests for the `Memories` interjection
(`Session._build_memories_interjection`/`Session.send_turn`)."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.api_provider import ProviderResponse
from klorb.message import Message
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.memory import common as memory_common_module
from klorb.tools.memory.common import Namespace, memory_namespace_dir
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    trusted: bool = True,
    provider: MagicMock | None = None,
    with_tool_registry: bool = True,
) -> tuple[Session, ToolSetupContext]:
    """Build a `Session` whose memory namespaces resolve under `tmp_path`, with a real
    `ToolRegistry` (so `ListMemories` is actually discovered) unless `with_tool_registry` is
    `False`, plus the matching `ToolSetupContext` a test can hand to `memory_namespace_dir()` to
    seed memory files in the same place the `Session` will look."""
    monkeypatch.setattr(memory_common_module, "get_klorb_data_dir", lambda: tmp_path / "data")
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    config = SessionConfig(
        model="some/model", workspace=Workspace(path=workspace_root, trusted=trusted))
    process_config = ProcessConfig()
    tool_registry = ToolRegistry.discover_tools(process_config, config) if with_tool_registry else None
    session = Session(
        config,
        provider=provider if provider is not None else MagicMock(),
        process_config=process_config,
        tool_registry=tool_registry,
    )
    context = ToolSetupContext(process_config=process_config, session_config=config)
    return session, context


def _write_memory(context: ToolSetupContext, namespace: Namespace, filename: str, content: str) -> None:
    namespace_dir = memory_namespace_dir(context, namespace)
    namespace_dir.mkdir(parents=True, exist_ok=True)
    (namespace_dir / filename).write_text(content)


def _user_messages(session: Session) -> list[Message]:
    return [m for m in session.messages if m.role == "user"]


def _reply(content: str = "ok") -> ProviderResponse:
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


def test_no_tool_registry_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session, _context = _build(tmp_path, monkeypatch, with_tool_registry=False)
    assert session._build_memories_interjection() is None


def test_empty_namespaces_prompt_create_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _context = _build(tmp_path, monkeypatch)
    body = session._build_memories_interjection()

    assert body is not None
    assert "You have not recorded any memories about your user." in body
    assert 'CreateMemory(namespace="global")' in body
    assert "You have not recorded any memories about this project." in body
    assert 'CreateMemory(namespace="workspace")' in body


def test_lists_global_and_workspace_memory_topics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, context = _build(tmp_path, monkeypatch)
    _write_memory(context, "global", "user.md", "User prefers concise replies\nbody\n")
    _write_memory(context, "workspace", "convention.md", "Uses tabs not spaces\nbody\n")

    body = session._build_memories_interjection()

    assert body is not None
    assert "* user.md: User prefers concise replies" in body
    assert 'ReadMemory(namespace="global")' in body
    assert "* convention.md: Uses tabs not spaces" in body
    assert 'ReadMemory(namespace="workspace")' in body


def test_workspace_section_omitted_when_untrusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, context = _build(tmp_path, monkeypatch, trusted=False)
    _write_memory(context, "global", "user.md", "User prefers concise replies\n")

    body = session._build_memories_interjection()

    assert body is not None
    assert "your user" in body
    assert "this project" not in body
    assert "workspace" not in body


def test_memory_toc_content_is_folded_into_the_interjection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, context = _build(tmp_path, monkeypatch)
    _write_memory(context, "global", "MEMORY.md", "TOC\nSee user.md for preferences\n")

    body = session._build_memories_interjection()

    assert body is not None
    assert "table of contents" in body
    assert '<MemoryTableOfContents namespace="global">' in body
    assert "</MemoryTableOfContents>" in body
    assert "See user.md for preferences" in body
    assert "This was truncated" not in body


def test_memory_toc_absent_when_no_memory_md_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, context = _build(tmp_path, monkeypatch)
    _write_memory(context, "global", "user.md", "User prefers concise replies\n")

    body = session._build_memories_interjection()

    assert body is not None
    assert "table of contents" not in body


def test_memory_toc_is_capped_at_the_auto_read_line_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, context = _build(tmp_path, monkeypatch)
    content = "TOC\n" + "\n".join(f"L{i}" for i in range(1, 60))
    _write_memory(context, "global", "MEMORY.md", content)

    body = session._build_memories_interjection()

    assert body is not None
    assert "L49" in body
    assert "L50" not in body


def test_memory_toc_truncation_names_the_resuming_read_memory_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, context = _build(tmp_path, monkeypatch)
    content = "TOC\n" + "\n".join(f"L{i}" for i in range(1, 60))
    _write_memory(context, "global", "MEMORY.md", content)

    body = session._build_memories_interjection()

    assert body is not None
    assert "This was truncated at 50 lines" in body
    assert 'ReadMemory(namespace="global", filename="MEMORY.md", start_line=51)' in body


def test_memory_toc_omitted_for_untrusted_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, context = _build(tmp_path, monkeypatch, trusted=False)
    _write_memory(context, "workspace", "MEMORY.md", "TOC\n")

    body = session._build_memories_interjection()

    assert body is not None
    assert "table of contents" not in body


def test_list_memories_failure_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any `ListMemories` failure -- not just a permission denial, which can no longer happen
    here since a read is always allowed -- drops the interjection instead of raising."""
    from klorb.tools.memory.list_memories import ListMemoriesTool

    def _boom(self: ListMemoriesTool, args: dict) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(ListMemoriesTool, "apply", _boom)
    session, _context = _build(tmp_path, monkeypatch)
    assert session._build_memories_interjection() is None


def test_send_turn_prepends_memories_interjection_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session, context = _build(tmp_path, monkeypatch, provider=mock_provider)
    _write_memory(context, "global", "user.md", "User prefers concise replies\n")

    session.send_turn("first")
    session.send_turn("second")

    user_msgs = _user_messages(session)
    assert len(user_msgs) == 2
    assert '<SystemInterjection subject="Memories">' in user_msgs[0].content
    assert "user.md" in user_msgs[0].content
    assert '<SystemInterjection subject="Memories">' not in user_msgs[1].content
    assert session._memories_seeded is True
