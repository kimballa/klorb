# © Copyright 2026 Aaron Kimball
"""Tests for the turn/tool hook points Phase 3 wires up: `onSubmitUserPrompt`/`onAgentTurnEnd`
in `klorb.session.mixins.turns._dispatch_turn` (root session only), `onToolUse`/`onToolResult`
in `klorb.session.mixins.tool_execution._run_tool_calls`, and the `Session.
_deliver_chained_hook_message`/`max_chained_hook_turns` chained-turn safety cap a `chat`
handler relies on."""
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import fixtures.sample_tools as sample_tools_package
import pytest

from klorb.api_provider import ProviderResponse, ResponseAborted
from klorb.hooks.config import HookConfig, HookConfigFilter
from klorb.message import Message, ToolCallRequest
from klorb.permissions.directory_access import DirRules
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.session.mixins.turns import HookDeniedTurnError
from klorb.token_estimate import estimate_tokens
from klorb.tools.registry import ToolRegistry
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


def _process_config(
    workspace_root: Path, make_session_config: Callable[..., SessionConfig],
    hooks: dict[str, list[HookConfig]],
) -> ProcessConfig:
    session = make_session_config(
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


def test_onsubmituserprompt_rewrites_the_user_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
        "onSubmitUserPrompt": [HookConfig(type="bash", shell='echo \'{"message": "rewritten"}\'')],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)

    session.send_turn("original prompt")

    user_message = next(m for m in session.messages if m.role == "user")
    assert user_message.content == "rewritten"
    assert user_message.num_tokens == estimate_tokens("rewritten")


def test_onsubmituserprompt_denial_raises_and_marks_the_user_message_as_errored(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
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


def test_onsubmituserprompt_is_a_noop_for_a_subagent_turn(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """`onSubmitUserPrompt` fires for the root session only -- a subagent's own turn goes
    through the exact same `_dispatch_turn`, so this proves the `self.parent is None` guard,
    not just that the hook exists."""
    process_config = _process_config(tmp_path, make_session_config, {
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


# --- onAgentTurnEnd / _deliver_chained_hook_message / max_chained_hook_turns ---


def test_onagentturnend_chat_handler_chains_turns_until_the_cap_trips(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
        "onAgentTurnEnd": [HookConfig(type="chat", prompt="keep going")],
    })
    provider = MagicMock()
    # 1 original turn + config.session.max_chained_hook_turns (default 5) chained ones.
    provider.send_prompt.side_effect = [_reply(f"reply {i}") for i in range(6)]
    session = Session(process_config.session, provider=provider, process_config=process_config)

    # A bare send_turn() only runs the one turn and leaves the rest queued -- run_one_shot()
    # owns the drain-and-resubmit loop that actually chains them (see docs/adrs/00186).
    session.run_one_shot("go")

    assert provider.send_prompt.call_count == 6
    user_messages = [m.content for m in session.messages if m.role == "user"]
    assert user_messages[0].endswith("go")
    assert user_messages[1:] == ["keep going"] * 5


def test_max_chained_hook_turns_zero_disables_chaining(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
        "onAgentTurnEnd": [HookConfig(type="chat", prompt="keep going")],
    })
    process_config.session.max_chained_hook_turns = 0
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)

    session.run_one_shot("go")

    assert provider.send_prompt.call_count == 1


def test_max_chained_hook_turns_negative_means_unlimited(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
        "onAgentTurnEnd": [
            HookConfig(
                type="chat", prompt="keep going",
                # Excludes the final "STOP" reply (negative lookahead) so the chain ends
                # naturally instead of genuinely running forever.
                filter=HookConfigFilter(pattern=r"^(?!STOP$).*")),
        ],
    })
    process_config.session.max_chained_hook_turns = -1
    provider = MagicMock()
    # 1 original + 7 chained "cont" replies -- past the default cap of 5 -- then a "STOP" reply
    # the filter excludes, ending the chain.
    provider.send_prompt.side_effect = [_reply("cont") for _ in range(7)] + [_reply("STOP")]
    session = Session(process_config.session, provider=provider, process_config=process_config)

    session.run_one_shot("go")

    assert provider.send_prompt.call_count == 8


def test_aborted_turn_resets_the_chained_hook_counter(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
        "onAgentTurnEnd": [HookConfig(type="chat", prompt="keep going")],
    })
    provider = MagicMock()
    # Two successful chained turns, then an abort on the third -- _fire_agent_turn_end_hook is
    # never reached for an aborted turn, so nothing explicitly resets the counter; only
    # _dispatch_turn's own generic entry-point reset can.
    provider.send_prompt.side_effect = [_reply("r0"), _reply("r1"), ResponseAborted()]
    session = Session(process_config.session, provider=provider, process_config=process_config)

    with pytest.raises(ResponseAborted):
        session.run_one_shot("go")

    # A later, independent chain gets the full cap again, not an already-part-spent budget.
    provider.send_prompt.side_effect = [_reply(f"r{i}") for i in range(6)]
    session.run_one_shot("resume")

    assert provider.send_prompt.call_count == 3 + 6


def test_onagentturnend_reset_session_resets_the_conversation_in_place(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
        "onAgentTurnEnd": [
            HookConfig(
                type="bash",
                shell='echo \'{"message": "fresh start", "reset_session": true}\'',
                # Filtered to the original turn's own reply so the reset conversation's own
                # first turn (whose reply is "second") doesn't trigger another reset -- an
                # unfiltered config would reset forever, since `_reset_state()` re-arms
                # `max_chained_hook_turns` back to `0` on every reset.
                filter=HookConfigFilter(matches="first")),
        ],
    })
    provider = MagicMock()
    provider.send_prompt.side_effect = [_reply("first"), _reply("second")]
    session = Session(process_config.session, provider=provider, process_config=process_config)
    original_id = session.id

    # run_one_shot(), not send_turn(): the reset's "fresh start" continuation is delivered via
    # _deliver_chained_hook_message like any other chained message, so it needs a drain loop to
    # actually run.
    session.run_one_shot("go")

    assert session.id == original_id
    assert provider.send_prompt.call_count == 2
    user_messages = [m.content for m in session.messages if m.role == "user"]
    assert len(user_messages) == 1
    assert user_messages[0].endswith("fresh start")


def test_onagentturnend_reset_session_also_fires_onsessionend_with_resetsession_event(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    output_path = tmp_path / "output.json"
    process_config = _process_config(tmp_path, make_session_config, {
        "onAgentTurnEnd": [
            HookConfig(
                type="bash",
                shell='echo \'{"message": "fresh start", "reset_session": true}\'',
                filter=HookConfigFilter(matches="first")),
        ],
        "onSessionEnd": [
            HookConfig(
                type="bash",
                shell=(
                    'python3 -c \'import sys, json; data = json.load(sys.stdin); '
                    f'open("{output_path}", "w").write(json.dumps(data))\'; '
                    'echo \'{}\'')),
        ],
    })
    provider = MagicMock()
    provider.send_prompt.side_effect = [_reply("first"), _reply("second")]
    session = Session(process_config.session, provider=provider, process_config=process_config)

    session.run_one_shot("go")

    data = json.loads(output_path.read_text())
    assert data["hook"] == "onSessionEnd"
    assert data["reason"] == "ResetSession"


def test_onagentturnend_reset_session_without_a_message_is_ignored(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
        "onAgentTurnEnd": [
            HookConfig(type="bash", shell='echo \'{"reset_session": true}\''),
        ],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = Session(process_config.session, provider=provider, process_config=process_config)
    original_id = session.id

    session.send_turn("go")

    assert session.id == original_id
    assert provider.send_prompt.call_count == 1
    user_messages = [m.content for m in session.messages if m.role == "user"]
    assert len(user_messages) == 1
    assert user_messages[0].endswith("go")


def test_onagentturnend_is_a_noop_for_a_subagent_turn(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
        "onAgentTurnEnd": [HookConfig(type="chat", prompt="keep going")],
    })
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    root = Session(process_config.session, provider=provider, process_config=process_config)
    child = Session(
        process_config.session.model_copy(), provider=provider, process_config=process_config, parent=root)

    child.send_turn("hi", resolve_mentions=False)

    assert provider.send_prompt.call_count == 1


# --- onToolUse / onToolResult ---


def _tool_registry(process_config: ProcessConfig, config: SessionConfig) -> ToolRegistry:
    return ToolRegistry.discover_tools(process_config, config, package=sample_tools_package)


def test_ontooluse_rewrites_tool_args_before_the_tool_runs(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
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


def test_ontooluse_deny_blocks_the_call(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
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


def test_ontoolresult_rewrites_the_response_content(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    process_config = _process_config(tmp_path, make_session_config, {
        "onToolResult": [HookConfig(type="bash", shell='echo \'{"tool_result": "rewritten result"}\'')],
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
    assert envelope["response_body"] == "rewritten result"
