# © Copyright 2026 Aaron Kimball
"""Tests for the session-controls surface `klorb.server.klorb_agent`/`klorb.server.turn_bridge`
add over ACP: session modes (permission framework), model/thinking session config, session
naming, token-usage notifications, and the `_klorb/sessionStats`/`_klorb/trustWorkspace`/
`_klorb/reloadSkills`/`_klorb/setSessionTitle` ext requests. See docs/specs/klorb-server.md."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import acp
import pytest
from server.acp_harness import AcpHarness, build_acp_harness

from klorb.api_provider import ApiProvider, ProviderResponse
from klorb.message import Message
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.session_naming import SessionName
from klorb.workspace import TrustManager
from klorb.workspace.session_store import find_recent_session, read_sessions_index


def _reply(content: str = "model reply", num_tokens: int = 5, prompt_tokens: int = 10) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=num_tokens,
            processing_state="complete", timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=prompt_tokens,
    )


def _plain_provider(**reply_kwargs: Any) -> MagicMock:
    provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ) -> ProviderResponse:
        if on_chunk is not None:
            on_chunk("hi")
        return _reply(**reply_kwargs)

    provider.send_prompt.side_effect = fake_send_prompt
    return provider


@pytest.fixture(autouse=True)
def _isolate_user_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `user_config_path()` at a temp location so `persist_session_default` calls in
    `_ext_set_session_config` never touch the developer's real config file."""
    monkeypatch.setattr("klorb.server.klorb_agent.user_config_path",
                        lambda: tmp_path / "klorb-config.json")


@pytest.fixture
async def make_harness(tmp_path: Path, make_session_config: Callable[..., SessionConfig]):
    """Factory fixture: `await make_harness(provider=...)` returns a running `AcpHarness`
    wired to an isolated `TrustManager` (so no test touches the real `KLORB_DATA_DIR`), closed
    automatically at teardown if the test hasn't already closed it."""
    harnesses: list[AcpHarness] = []

    async def _make(
        provider: ApiProvider | None = None
    ) -> AcpHarness:
        trust_manager = TrustManager(path=tmp_path / "projects.json")
        harness = await build_acp_harness(ProcessConfig(session=make_session_config()), provider=provider,
            trust_manager=trust_manager)
        harnesses.append(harness)
        return harness

    yield _make

    for harness in harnesses:
        if not harness.server_task.done():
            await harness.aclose()


async def _new_session(harness: AcpHarness, cwd: Path) -> str:
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)
    response = await harness.client.new_session(cwd=str(cwd), mcp_servers=[])
    return str(response.session_id)


async def test_new_session_advertises_modes_and_workspace_meta(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    await harness.client.initialize(protocol_version=acp.PROTOCOL_VERSION)

    response = await harness.client.new_session(cwd=str(tmp_path), mcp_servers=[])

    assert response.modes is not None
    assert response.modes.current_mode_id == "ask"
    assert {mode.id for mode in response.modes.available_modes} == {"ask", "auto", "deny"}
    assert response.field_meta is not None
    workspace_meta = response.field_meta["klorb"]["workspace"]
    assert workspace_meta == {"path": str(tmp_path.resolve()), "trusted": False}
    assert response.field_meta["klorb"]["title"] is None


async def test_set_session_mode_changes_config_and_broadcasts_current_mode_update(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    await harness.client.set_session_mode(mode_id="auto", session_id=session_id)

    session = harness.server.agent.session
    assert session is not None
    assert session.config.permission_framework == "auto"
    assert session._pending_permission_framework_interjection is not None

    mode_updates = [
        update.update for update in harness.harness_client.session_updates
        if update.update.session_update == "current_mode_update"
    ]
    assert len(mode_updates) == 1
    assert mode_updates[0].current_mode_id == "auto"


async def test_set_session_mode_with_unknown_mode_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    with pytest.raises(acp.RequestError):
        await harness.client.set_session_mode(mode_id="bogus", session_id=session_id)


async def test_get_session_config_lists_registry_models_and_current_values(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    result = await harness.client.ext_method("klorb/getSessionConfig", {"sessionId": session_id})

    session = harness.server.agent.session
    assert session is not None
    assert result["model"]["current"] == session.config.model
    assert {entry["id"] for entry in result["model"]["available"]} >= {session.config.model}
    assert result["thinking"] == {
        "enabled": session.config.thinking_enabled, "effort": session.config.thinking_effort}


async def test_set_session_config_mutates_model_and_thinking(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    result = await harness.client.ext_method("klorb/setSessionConfig", {
        "sessionId": session_id, "model": "some/other-model",
        "thinking": {"enabled": False, "effort": "low"},
    })

    session = harness.server.agent.session
    assert session is not None
    assert session.config.model == "some/other-model"
    assert session.config.thinking_enabled is False
    assert session.config.thinking_effort == "low"
    assert result["model"]["current"] == "some/other-model"
    assert result["thinking"] == {"enabled": False, "effort": "low"}


async def test_set_session_config_propagates_to_process_config_and_new_session(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    """`_klorb/setSessionConfig` updates the process-level default (`process_config.session`)
    so that a subsequent `session/new` inherits the user's chosen model/thinking settings."""
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    await harness.client.ext_method("klorb/setSessionConfig", {
        "sessionId": session_id, "model": "sticky/model",
        "thinking": {"enabled": True, "effort": "medium"},
    })

    process_session = harness.server.agent._process_config.session
    assert process_session.model == "sticky/model"
    assert process_session.thinking_enabled is True
    assert process_session.thinking_effort == "medium"

    session_id_2 = await _new_session(harness, tmp_path)
    session_2 = harness.server.agent.session
    assert session_2 is not None
    assert session_2.config.model == "sticky/model"
    assert session_2.config.thinking_enabled is True
    assert session_2.config.thinking_effort == "medium"

    result = await harness.client.ext_method("klorb/getSessionConfig", {"sessionId": session_id_2})
    assert result["model"]["current"] == "sticky/model"
    assert result["thinking"] == {"enabled": True, "effort": "medium"}


async def test_set_session_config_with_invalid_effort_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    with pytest.raises(acp.RequestError):
        await harness.client.ext_method(
            "klorb/setSessionConfig", {"sessionId": session_id, "thinking": {"effort": "extreme"}})


async def test_set_session_title_renames_trims_and_persists(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    trust_manager.register_project(tmp_path, trusted=True)
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    session = harness.server.agent.session
    assert session is not None

    result = await harness.client.ext_method(
        "klorb/setSessionTitle", {"sessionId": session_id, "title": "  My Renamed Session  "})

    assert result == {"title": "My Renamed Session"}
    assert session.name == "My Renamed Session"
    assert session.session_naming_pending is False
    index = read_sessions_index(session.config.workspace)
    entry = find_recent_session(index, session_id)
    assert entry is not None
    assert entry.title == "My Renamed Session"


async def test_set_session_title_with_blank_string_clears_to_none(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)
    session = harness.server.agent.session
    assert session is not None
    session.name = "Existing title"

    result = await harness.client.ext_method(
        "klorb/setSessionTitle", {"sessionId": session_id, "title": "   "})

    assert result == {"title": None}
    assert session.name is None


async def test_set_session_title_with_non_string_title_is_a_json_rpc_error(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    with pytest.raises(acp.RequestError):
        await harness.client.ext_method(
            "klorb/setSessionTitle", {"sessionId": session_id, "title": 7})


def _title_updates(harness: AcpHarness) -> list[Any]:
    return [
        update.update for update in harness.harness_client.session_updates
        if update.update.session_update == "session_info_update"
    ]


async def _wait_for_title_update(harness: AcpHarness) -> None:
    """Poll for a `session_info_update` to arrive: the session-naming classifier now runs on
    `Session`'s own background thread (`SessionCoreMixin._start_session_naming`) and delivers its
    result directly onto the event loop (`TurnBridge._send_session_name_update`, via `asyncio.
    run_coroutine_threadsafe`) rather than through the per-turn update queue `prompt()` itself
    already awaited -- so it can still be pending after `prompt()` has returned."""
    for _ in range(500):
        if _title_updates(harness):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("session_info_update never arrived")


async def test_session_naming_fires_title_update_once_on_success(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "klorb.session.mixins.core.generate_session_name",
        lambda *args, **kwargs: SessionName(title="Fixing the bug"))
    harness = await make_harness(provider=_plain_provider())
    session_id = await _new_session(harness, tmp_path)

    await harness.client.prompt(session_id=session_id, prompt=[acp.text_block("hi")])
    await _wait_for_title_update(harness)

    title_updates = _title_updates(harness)
    assert len(title_updates) == 1
    assert title_updates[0].title == "Fixing the bug"


async def test_session_naming_fires_title_update_with_none_on_failure(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "klorb.session.mixins.core.generate_session_name", lambda *args, **kwargs: None)
    harness = await make_harness(provider=_plain_provider())
    session_id = await _new_session(harness, tmp_path)

    await harness.client.prompt(session_id=session_id, prompt=[acp.text_block("hi")])
    await _wait_for_title_update(harness)

    title_updates = _title_updates(harness)
    assert len(title_updates) == 1
    assert title_updates[0].title is None


async def test_usage_notification_after_turn_matches_total_tokens_used(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Naming would otherwise fire a real (network-bound) classifier call against the mock
    # provider's default Mock return value; short-circuit it so this test only exercises usage.
    monkeypatch.setattr("klorb.session.mixins.core.generate_session_name", lambda *args, **kwargs: None)
    harness = await make_harness(provider=_plain_provider())
    session_id = await _new_session(harness, tmp_path)

    await harness.client.prompt(session_id=session_id, prompt=[acp.text_block("hi")])

    session = harness.server.agent.session
    assert session is not None
    usage_calls = [
        params for method, params in harness.harness_client.ext_notification_calls
        if method == "klorb/usage"
    ]
    # At least one usage notification (the turn-end one), possibly more from mid-turn
    # intermediate updates (tool-call completions, chunk throttling).
    assert len(usage_calls) >= 1
    # The last usage notification reflects the session's final token tally.
    last_usage = usage_calls[-1]
    assert last_usage["sessionId"] == session_id
    assert last_usage["usedTokens"] == session.total_tokens_used()
    assert last_usage["maxTokens"] == session.max_context_window()
    assert last_usage["outputTokens"] == session.total_output_tokens_used()


async def test_session_stats_ext_method_returns_statistics_json(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("klorb.session.mixins.core.generate_session_name", lambda *args, **kwargs: None)
    harness = await make_harness(provider=_plain_provider())
    session_id = await _new_session(harness, tmp_path)
    await harness.client.prompt(session_id=session_id, prompt=[acp.text_block("hi")])

    result = await harness.client.ext_method("klorb/sessionStats", {"sessionId": session_id})

    session = harness.server.agent.session
    assert session is not None
    assert result == session.statistics.model_dump(mode="json")
    assert result["user_messages"] == 1


async def test_reload_skills_ext_method_returns_skill_count(
    make_harness: Callable[..., Any], tmp_path: Path,
) -> None:
    harness = await make_harness(provider=MagicMock())
    session_id = await _new_session(harness, tmp_path)

    result = await harness.client.ext_method("klorb/reloadSkills", {"sessionId": session_id})

    assert isinstance(result["skillCount"], int)


async def test_trust_workspace_ext_method_trusts_and_seeds_context_files(
    make_harness: Callable[..., Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("klorb.session.mixins.core.generate_session_name", lambda *args, **kwargs: None)
    (tmp_path / "AGENTS.md").write_text("Be careful with tests.")
    provider = MagicMock()
    captured: list[list[Message]] = []

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ) -> ProviderResponse:
        captured.append(messages)
        if on_chunk is not None:
            on_chunk("hi")
        return _reply()

    provider.send_prompt.side_effect = fake_send_prompt
    harness = await make_harness(provider=provider)
    session_id = await _new_session(harness, tmp_path)
    session = harness.server.agent.session
    assert session is not None
    assert not session.config.workspace.trusted

    result = await harness.client.ext_method("klorb/trustWorkspace", {"sessionId": session_id})

    assert result["workspace"] == {"path": str(tmp_path.resolve()), "trusted": True}
    assert session.config.workspace.trusted

    await harness.client.prompt(session_id=session_id, prompt=[acp.text_block("hi")])

    assert captured, "provider.send_prompt was never called"
    user_message = next(m for m in captured[-1] if m.role == "user")
    assert 'subject="ProjectGuidance"' in user_message.content
    assert "Be careful with tests." in user_message.content
