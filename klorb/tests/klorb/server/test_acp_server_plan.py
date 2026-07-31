# © Copyright 2026 Aaron Kimball
"""Tests for the ACP server's chainlink `plan` session updates -- `TurnBridge`'s post-tool-call
refresh trigger and `KlorbAcpAgent`'s initial-snapshot-on-`session/new` trigger. `ChainlinkClient`
is faked at the seam (`klorb.tools.tasks.todo_create`/`todo_update`/`klorb.server.turn_bridge`'s
own imported bindings) rather than shelling out to the real `chainlink` binary -- the tools' own
chainlink-driving behavior is already covered by `tests/klorb/tools/tasks/`. See
docs/specs/klorb-server.md's "Chainlink task-plan updates" section."""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import acp
import pytest
from server.acp_harness import AcpHarness, build_acp_harness

from klorb.api_provider import ApiProvider, ProviderResponse
from klorb.message import Message, ToolCallRequest
from klorb.process_config import ProcessConfig
from klorb.server import turn_bridge
from klorb.tools.tasks import todo_create, todo_update
from klorb.tools.tasks.common import ChainlinkError, chainlink_available
from klorb.workspace import TrustManager

requires_chainlink = pytest.mark.skipif(
    not chainlink_available(),
    reason="chainlink binary not found on PATH or ~/.cargo/bin -- required for the TASKS tool "
    "category to be registered, even though ChainlinkClient itself is faked below")


def _reply(content: str = "done", num_tokens: int = 3, prompt_tokens: int = 10) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=num_tokens,
            processing_state="complete", timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=prompt_tokens,
    )


def _tool_call_reply(calls: list[tuple[str, str, dict[str, Any]]]) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="", role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="tool_calls",
            tool_calls=[
                ToolCallRequest(id=call_id, name=name, arguments=json.dumps(args))
                for call_id, name, args in calls
            ],
        ),
        prompt_tokens=10,
    )


class _FakeChainlinkClient:
    """Test double for `klorb.tools.tasks.common.ChainlinkClient`: a single in-memory issue
    store (ignoring `label` scoping -- these tests only ever run one session), so a scripted
    turn's `Todo*` tool calls and the ACP server's own plan-refresh fetch see the same state a
    real chainlink sqlite file would provide, without shelling out to the real binary."""

    def __init__(self, context: Any) -> None:
        del context

    _issues: dict[int, dict[str, Any]]
    _next_id: int

    def list_issues(self, *, status: str = "open") -> list[dict[str, Any]]:
        return [
            dict(issue) for issue in self._issues.values()
            if status == "all" or issue["status"] == status
        ]

    def show_issue(self, issue_id: int) -> dict[str, Any]:
        return dict(self._issues[issue_id])

    def create_issue(
        self, title: str, *, description: str | None = None, priority: str = "medium",
    ) -> int:
        issue_id = self._next_id
        type(self)._next_id += 1
        self._issues[issue_id] = {
            "id": issue_id, "title": title, "description": description, "priority": priority,
            "status": "open", "blocked_by": [], "comments": [],
        }
        return issue_id

    def update_issue(
        self, issue_id: int, *, title: str | None = None, description: str | None = None,
        priority: str | None = None,
    ) -> None:
        issue = self._issues[issue_id]
        if title is not None:
            issue["title"] = title
        if description is not None:
            issue["description"] = description
        if priority is not None:
            issue["priority"] = priority

    def close_issue(self, issue_id: int) -> None:
        self._issues[issue_id]["status"] = "closed"

    def reopen_issue(self, issue_id: int) -> None:
        self._issues[issue_id]["status"] = "open"

    def block(self, issue_id: int, blocker_id: int) -> None:
        self._issues[issue_id]["blocked_by"].append(blocker_id)

    def unblock(self, issue_id: int, blocker_id: int) -> None:
        self._issues[issue_id]["blocked_by"].remove(blocker_id)

    def comment(self, issue_id: int, text: str, *, kind: str = "note") -> None:
        self._issues[issue_id]["comments"].append({"content": text, "kind": kind})


def _make_fake_chainlink_client_class(
    seed_issues: dict[int, dict[str, Any]] | None = None,
) -> type[_FakeChainlinkClient]:
    """A fresh `_FakeChainlinkClient` subclass with its own issue store, so each test gets
    isolated state even though the store lives on the class (every `Tool.apply()` constructs a
    new `ChainlinkClient` instance, exactly like the real one)."""
    issues = dict(seed_issues or {})
    next_id = max(issues.keys(), default=0) + 1

    class FakeChainlinkClient(_FakeChainlinkClient):
        _issues = issues
        _next_id = next_id

    return FakeChainlinkClient


class _RaisingChainlinkClient:
    def __init__(self, context: Any) -> None:
        del context
        raise ChainlinkError("simulated chainlink failure")


@pytest.fixture
async def make_harness(tmp_path: Path):
    """Factory fixture, mirroring `test_acp_server_tool_calls.py`'s own."""
    harnesses: list[AcpHarness] = []

    async def _make(provider: ApiProvider | None = None) -> AcpHarness:
        trust_manager = TrustManager(path=tmp_path / "projects.json")
        harness = await build_acp_harness(ProcessConfig(), provider=provider, trust_manager=trust_manager)
        harnesses.append(harness)
        return harness

    yield _make

    for harness in harnesses:
        if not harness.server_task.done():
            await harness.aclose()


def _plan_updates(harness: AcpHarness) -> list[Any]:
    return [
        notification.update for notification in harness.harness_client.session_updates
        if notification.update.session_update == "plan"
    ]


@requires_chainlink
async def test_todo_create_then_close_emits_two_plan_updates_tracking_fake_state(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cls = _make_fake_chainlink_client_class()
    monkeypatch.setattr(todo_create, "ChainlinkClient", fake_cls)
    monkeypatch.setattr(todo_update, "ChainlinkClient", fake_cls)
    monkeypatch.setattr(turn_bridge, "ChainlinkClient", fake_cls)

    (tmp_path / "workspace").mkdir()
    workspace = tmp_path / "workspace"

    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "TodoCreate", {"title": "Write the spec"})]),
        _tool_call_reply([("call_2", "TodoUpdate", {"id": 1, "close": True})]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    response = await harness.client.prompt(
        session_id=session_response.session_id,
        prompt=[acp.text_block("create and close a todo")])

    assert response.stop_reason == "end_turn"
    plan_updates = _plan_updates(harness)
    assert len(plan_updates) == 2

    after_create = plan_updates[0]
    assert len(after_create.entries) == 1
    assert after_create.entries[0].content == "#1 Write the spec"
    # TodoCreate auto-activates the new issue as the session's current tracked task since none
    # was set yet -- see klorb.tools.tasks._util.maybe_activate_task.
    assert after_create.entries[0].status == "in_progress"

    after_close = plan_updates[1]
    assert len(after_close.entries) == 1
    assert after_close.entries[0].status == "completed"


async def test_turn_with_no_task_tool_calls_emits_no_plan_updates(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    (tmp_path / "workspace").mkdir()
    workspace = tmp_path / "workspace"

    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("hi there")]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("hello")])

    assert _plan_updates(harness) == []


@requires_chainlink
async def test_new_session_with_a_pre_existing_db_emits_the_initial_snapshot(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cls = _make_fake_chainlink_client_class(
        seed_issues={1: {
            "id": 1, "title": "Pre-existing task", "description": None, "priority": "medium",
            "status": "open", "blocked_by": [], "comments": [],
        }})
    monkeypatch.setattr(turn_bridge, "ChainlinkClient", fake_cls)

    (tmp_path / "workspace").mkdir()
    workspace = tmp_path / "workspace"
    (workspace / ".chainlink").mkdir()
    (workspace / ".chainlink" / "issues.db").touch()

    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    plan_updates = _plan_updates(harness)
    assert len(plan_updates) == 1
    assert plan_updates[0].entries[0].content == "#1 Pre-existing task"


async def test_new_session_without_a_pre_existing_db_emits_no_initial_snapshot(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    (tmp_path / "workspace").mkdir()
    workspace = tmp_path / "workspace"

    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    assert _plan_updates(harness) == []


@requires_chainlink
async def test_chainlink_error_on_refresh_emits_no_plan_update_and_does_not_fail_the_turn(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cls = _make_fake_chainlink_client_class()
    monkeypatch.setattr(todo_create, "ChainlinkClient", fake_cls)
    monkeypatch.setattr(turn_bridge, "ChainlinkClient", _RaisingChainlinkClient)

    (tmp_path / "workspace").mkdir()
    workspace = tmp_path / "workspace"

    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "TodoCreate", {"title": "Write the spec"})]),
        _reply("done"),
    ]
    harness = await make_harness(provider=mock_provider)
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    session_response = await harness.client.new_session(cwd=str(workspace), mcp_servers=[])

    response = await harness.client.prompt(
        session_id=session_response.session_id, prompt=[acp.text_block("create a todo")])

    assert response.stop_reason == "end_turn"
    assert _plan_updates(harness) == []
    tool_call_updates = [
        notification.update for notification in harness.harness_client.session_updates
        if notification.update.session_update == "tool_call_update"
    ]
    assert len(tool_call_updates) == 1
    assert tool_call_updates[0].status == "completed"
