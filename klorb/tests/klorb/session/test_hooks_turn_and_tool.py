# © Copyright 2026 Aaron Kimball
"""Tests for the turn/tool hook points Phase 3 wires up: `onSubmitUserPrompt`/`onAgentTurnEnd`
in `klorb.session.mixins.turns._dispatch_turn` (root session only), `onToolUse`/`onToolResult`
in `klorb.session.mixins.tool_execution._run_tool_calls`, and the `Session.
start_turn_or_enqueue`/`max_chained_hook_turns` chained-turn safety cap a `chat` handler relies
on."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import fixtures.sample_tools as sample_tools_package
import pytest

from klorb.api_provider import ProviderResponse
from klorb.hooks.config import HookConfig
from klorb.message import Message, ToolCallRequest
from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, TurnEventHandlers
from klorb.session.events import QueuedMessage
from klorb.session.mixins.turns import HookDeniedTurnError
from klorb.token_estimate import estimate_tokens
from klorb.tools.registry import ToolRegistry
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


def _process_config(workspace_root: Path, hooks: dict[str, list[HookConfig]]) -> ProcessConfig:
    session = SessionConfig(
        workspace=Workspace(path=workspace_root, trusted=True),
        read_dirs=DirRules(allow=[workspace_root]),
        write_dirs=DirRules(allow=[workspace_root]))
    return ProcessConfig(session=session, hooks=hooks)


def _reply(content: str = "model reply") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=5)


def _tool_call_reply(call_id: str, tool_name: str, arguments: str) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="", role="assistant", num_tokens=3, processing_state="complete",
            timestamp=datetime.now(), finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=call_id, name=tool_name, arguments=arguments)]),
        prompt_tokens=5)


# --- onSubmitUserPrompt ---


def test_onsubmituserprompt_rewrites_the_user_message(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onSubmitUserPrompt": [HookConfig(type="bash", shell='echo \'{"message": "rewritten"}\'')],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)

    session.send_turn("original prompt")

    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.content == "rewritten"
    assert user_message.num_tokens == estimate_tokens("rewritten")


def test_onsubmituserprompt_denial_raises_and_marks_the_user_message_as_errored(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onSubmitUserPrompt": [
            HookConfig(type="bash", shell='echo \'{"success": false, "message": "blocked by policy"}\''),
        ],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)

    with pytest.raises(HookDeniedTurnError, match="blocked by policy"):
        session.send_turn("original prompt")

    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.processing_state == "error"
    assert user_message.last_error == "blocked by policy"
    provider.send_prompt.assert_not_called()


def test_onsubmituserprompt_is_a_noop_for_a_subagent_turn(tmp_path: Path) -> None:
    """`onSubmitUserPrompt` fires for the root session only -- a subagent's own turn goes
    through the exact same `_dispatch_turn`, so this proves the `self.parent is None` guard,
    not just that the hook exists."""
    process_config = _process_config(tmp_path, {
        "onSubmitUserPrompt": [HookConfig(type="bash", shell='echo \'{"message": "rewritten"}\'')],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    root = Session(process_config.session, provider=provider, process_config=process_config)
    child = Session(
        process_config.session.model_copy(), provider=provider, process_config=process_config, parent=root)

    child.send_turn("original prompt", resolve_mentions=False)

    user_message = next(m for m in child.messages if m.role == "user")
    assert user_message.content.endswith("original prompt")
    assert user_message.content != "rewritten"


# --- onAgentTurnEnd / start_turn_or_enqueue / max_chained_hook_turns ---


def test_onagentturnend_chat_handler_chains_turns_until_the_cap_trips(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [HookConfig(type="chat", prompt="keep going")],
    })
    provider = MagicMock()
    # 1 original turn + config.session.max_chained_hook_turns (default 5) chained ones.
    provider.send_prompt.side_effect = [_reply(f"reply {i}") for i in range(6)]
    session = Session(process_config.session, provider=provider, process_config=process_config)

    session.send_turn("go")

    assert provider.send_prompt.call_count == 6
    user_messages = [m.content for m in session.messages if m.role == "user"]
    assert user_messages[0].endswith("go")
    assert user_messages[1:] == ["keep going"] * 5


def test_onagentturnend_clear_session_invokes_the_callback_instead_of_chaining(
    tmp_path: Path,
) -> None:
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [
            HookConfig(
                type="bash",
                shell='echo \'{"message": "fresh start", "clear_session": true}\''),
        ],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    received: list[str] = []
    session.on_clear_session_requested = received.append

    session.send_turn("go")

    assert received == ["fresh start"]
    # Only the original turn ran -- clear_session bypasses start_turn_or_enqueue's chaining.
    assert provider.send_prompt.call_count == 1


def test_onagentturnend_clear_session_falls_back_to_chaining_without_a_handler(
    tmp_path: Path,
) -> None:
    """Without a registered `on_clear_session_requested`, `clear_session` degrades to an
    ordinary chained turn -- and since the hook fires again on that follow-up turn's own end,
    it keeps chaining until `max_chained_hook_turns` (default 5) trips, the same safety cap
    an ordinary `chat` handler is bound by (see
    `test_onagentturnend_chat_handler_chains_turns_until_the_cap_trips`)."""
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [
            HookConfig(
                type="bash",
                shell='echo \'{"message": "fresh start", "clear_session": true}\''),
        ],
    })
    provider = MagicMock()
    provider.send_prompt.side_effect = [_reply(f"reply {i}") for i in range(6)]
    session = Session(process_config.session, provider=provider, process_config=process_config)

    session.send_turn("go")

    assert provider.send_prompt.call_count == 6
    user_messages = [m.content for m in session.messages if m.role == "user"]
    assert user_messages[1:] == ["fresh start"] * 5


def test_onagentturnend_clear_session_without_a_message_is_ignored(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [
            HookConfig(type="bash", shell='echo \'{"clear_session": true}\''),
        ],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    received: list[str] = []
    session.on_clear_session_requested = received.append

    session.send_turn("go")

    assert received == []
    assert provider.send_prompt.call_count == 1


def test_onagentturnend_is_a_noop_for_a_subagent_turn(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onAgentTurnEnd": [HookConfig(type="chat", prompt="keep going")],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    root = Session(process_config.session, provider=provider, process_config=process_config)
    child = Session(
        process_config.session.model_copy(), provider=provider, process_config=process_config, parent=root)

    child.send_turn("hi", resolve_mentions=False)

    assert provider.send_prompt.call_count == 1


def test_start_turn_or_enqueue_queues_when_a_turn_is_already_in_flight(tmp_path: Path) -> None:
    provider = MagicMock()
    session = Session(SessionConfig(workspace=Workspace(path=tmp_path, trusted=True)), provider=provider)
    session._current_turn_handlers = TurnEventHandlers()

    session.start_turn_or_enqueue("interjected")

    provider.send_prompt.assert_not_called()
    drained = session.drain_queued_messages()
    assert drained == [QueuedMessage(message_text="interjected")]


# --- onToolUse / onToolResult ---


def _tool_registry(process_config: ProcessConfig, config: SessionConfig) -> ToolRegistry:
    return ToolRegistry.discover_tools(process_config, config, package=sample_tools_package)


def test_ontooluse_rewrites_tool_args_before_the_tool_runs(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onToolUse": [HookConfig(type="bash", shell='echo \'{"tool_args": {"message": "rewritten"}}\'')],
    })
    provider = MagicMock()
    provider.send_prompt.side_effect = [
        _tool_call_reply("call_1", "echo", '{"message": "original"}'),
        _reply("final"),
    ]
    session = Session(
        process_config.session, provider=provider, process_config=process_config,
        tool_registry=_tool_registry(process_config, process_config.session))

    session.send_turn("please echo")

    tool_response = next(m for m in session.messages if m.role == "tool_response")
    envelope = json.loads(tool_response.content)
    assert envelope["response_body"] == "rewritten"


def test_ontooluse_deny_blocks_the_call(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onToolUse": [
            HookConfig(type="bash", shell='echo \'{"permission": "deny", "message": "blocked by policy"}\''),
        ],
    })
    provider = MagicMock()
    provider.send_prompt.side_effect = [
        _tool_call_reply("call_1", "echo", '{"message": "original"}'),
        _reply("final"),
    ]
    session = Session(
        process_config.session, provider=provider, process_config=process_config,
        tool_registry=_tool_registry(process_config, process_config.session))

    session.send_turn("please echo")

    tool_response = next(m for m in session.messages if m.role == "tool_response")
    envelope = json.loads(tool_response.content)
    assert envelope["is_error"] is True
    assert envelope["error_category"] == "permission"
    assert envelope["error_message"] == "blocked by policy"


def test_ontoolresult_rewrites_the_response_content(tmp_path: Path) -> None:
    process_config = _process_config(tmp_path, {
        "onToolResult": [HookConfig(type="bash", shell='echo \'{"message": "rewritten result"}\'')],
    })
    provider = MagicMock()
    provider.send_prompt.side_effect = [
        _tool_call_reply("call_1", "echo", '{"message": "original"}'),
        _reply("final"),
    ]
    session = Session(
        process_config.session, provider=provider, process_config=process_config,
        tool_registry=_tool_registry(process_config, process_config.session))

    session.send_turn("please echo")

    tool_response = next(m for m in session.messages if m.role == "tool_response")
    assert tool_response.content == "rewritten result"
