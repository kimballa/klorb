# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session."""
import io
import json
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from unittest import mock
from unittest.mock import MagicMock

import fixtures.sample_tools as sample_tools_package
import pytest
import yaml
from fixtures.sample_models import NO_SUCH_DIR, sample_model_registry
from PIL import Image

from klorb import process_config as process_config_module
from klorb.agents.runtime import SubagentHandle, SubagentTurnOutcome
from klorb.api_provider import ProviderResponse, ResponseAborted
from klorb.message import Message, MessageFragment, ToolCallRequest
from klorb.models.configured_model import ConfiguredModel
from klorb.models.model import Model
from klorb.models.registry import ModelRegistry
from klorb.permissions.directory_access import DirRules
from klorb.permissions.resource import PathResource, PermissionOverride, SkillResource
from klorb.permissions.table import MultiPermissionAskRequired, PermissionAskItem, PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.role import OperatorRole
from klorb.session import (
    DEFAULT_MAX_TOOL_CALLS_PER_TURN,
    MAX_TOOL_CALL_ROUNDS,
    PERMISSION_FRAMEWORK_INTERJECTIONS,
    THINKING_EFFORT_TOKEN_BUDGETS,
    PermissionDecision,
    Session,
    SessionConfig,
    ThinkingEffort,
    ToolCallEvent,
    ToolCallLimitExceeded,
    TurnEventHandlers,
    generate_session_id,
)
from klorb.session.events import QueuedMessage
from klorb.session_naming import SessionName
from klorb.system_prompt import DEFAULT_SYS_FILENAME, resolve_prompt_file
from klorb.token_estimate import estimate_tokens
from klorb.tools.registry import ToolRegistry
from klorb.workspace import Workspace

SESSION_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-[a-z]+-[a-z]+$")

# What Session._resolve_system_prompt() produces for the default (operator) role, and for a
# role with no prompt files on an unregistered model. Computed via the same resolve_prompt_file()
# the session uses, so these stay correct even if a developer's own user-tier override files exist.
OPERATOR_PROMPT = resolve_prompt_file("roles/operator/default.md")
DEFAULT_PROMPT = resolve_prompt_file(DEFAULT_SYS_FILENAME)

# What an operator-role session's resolved system prompt looks like when its "default walk"
# lands on default_sys.md (e.g. an unregistered model): the default prompt, then the role's own
# prompt layered on afterward inside an <AgentRole> tag — see Session._resolve_system_prompt().
COMPOSED_OPERATOR_PROMPT = f"{DEFAULT_PROMPT}\n\n<AgentRole>\n{OPERATOR_PROMPT}\n</AgentRole>"


def _with_metadata(
    prompt: str,
    model: str,
    knowledge_cutoff: str | None = None,
    claude_markdown: bool = False,
    claude_skills: bool = False,
) -> str:
    """Append the expected ``## Metadata`` section that `SystemPrompt.resolve()` adds."""
    metadata: dict[str, Any] = {"model": model}
    if knowledge_cutoff is not None:
        metadata["knowledgeCutoff"] = knowledge_cutoff
    metadata["config"] = {
        "compatibility.claudeMarkdown": claude_markdown,
        "compatibility.claudeSkills": claude_skills,
    }
    yaml_str = yaml.safe_dump(metadata, sort_keys=False)
    return f"{prompt}\n\n## Metadata\n\n```yaml\n{yaml_str}```"


# A role_name with no dedicated Role subclass and no roles/<name>/ prompt files anywhere, so
# resolution falls through the role tiers to the model-specific and default tiers.
ROLE_WITHOUT_PROMPT_FILES = "test-role-with-no-prompt-files"


def _reply(content: str = "model reply", num_tokens: int = 5, prompt_tokens: int = 10) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content,
            role="assistant",
            num_tokens=num_tokens,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason="stop",
        ),
        prompt_tokens=prompt_tokens,
    )


def _tool_call_reply(
    calls: list[tuple[str, str, str]], num_tokens: int = 3, prompt_tokens: int = 10,
) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="",
            role="assistant",
            num_tokens=num_tokens,
            processing_state="complete",
            timestamp=datetime.now(),
            finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=id_, name=name, arguments=args) for id_, name, args in calls],
        ),
        prompt_tokens=prompt_tokens,
    )


def _sample_tool_registry(config: SessionConfig) -> ToolRegistry:
    return ToolRegistry.discover_tools(ProcessConfig(), config, package=sample_tools_package)


def test_session_config_defaults() -> None:
    config = SessionConfig()

    assert config.interactive is True
    assert config.thinking_enabled is True
    assert config.thinking_effort == "high"


def test_session_saves_config(make_session_config: Callable[..., SessionConfig]) -> None:
    config = make_session_config(model="some/model", interactive=False)
    session = Session(config, provider=MagicMock())

    assert session.config is config


def test_session_role_defaults_to_operator(make_session_config: Callable[..., SessionConfig]) -> None:
    session = Session(make_session_config(), provider=MagicMock())

    assert isinstance(session.role, OperatorRole)


def test_session_builds_role_from_config_role_name(make_session_config: Callable[..., SessionConfig]) -> None:
    session = Session(make_session_config(role_name="auditor"), provider=MagicMock())

    assert session.role.name() == "auditor"


def test_generate_session_id_matches_expected_format() -> None:
    assert SESSION_ID_RE.match(generate_session_id())


def test_generate_session_id_is_unique_across_calls() -> None:
    assert generate_session_id() != generate_session_id()


def test_allocate_child_index_is_unique_under_concurrent_calls(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    """Two subagents constructed concurrently under the same parent must receive distinct
    `_child_index` values, never the same one."""
    parent = Session(make_session_config(), provider=MagicMock())
    barrier = threading.Barrier(20)

    def make_child() -> Session:
        barrier.wait(timeout=5.0)
        return Session(make_session_config(), provider=MagicMock(), parent=parent)

    children: list[Session] = []
    children_lock = threading.Lock()

    def make_child_and_record() -> None:
        child = make_child()
        with children_lock:
            children.append(child)

    workers = [threading.Thread(target=make_child_and_record) for _ in range(20)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5.0)

    indices = [child._child_index for child in children]
    assert len(indices) == len(set(indices)) == 20


def test_session_generates_id_when_not_given_explicitly(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config()
    session = Session(config, provider=MagicMock())

    assert SESSION_ID_RE.match(session.id)


def test_session_uses_explicitly_given_id(make_session_config: Callable[..., SessionConfig]) -> None:
    config = make_session_config()
    session = Session(config, provider=MagicMock(), session_id="my-custom-id")

    assert session.id == "my-custom-id"


def test_session_root_id_defaults_to_its_own_id(make_session_config: Callable[..., SessionConfig]) -> None:
    config = make_session_config()
    session = Session(config, provider=MagicMock(), session_id="my-custom-id")

    assert session.root_id == "my-custom-id"


def test_session_uses_explicitly_given_root_id(make_session_config: Callable[..., SessionConfig]) -> None:
    config = make_session_config()
    session = Session(config, provider=MagicMock(), session_id="child-id", root_id="root-id")

    assert session.id == "child-id"
    assert session.root_id == "root-id"


def test_get_chainlink_label_returns_group_prefixed_root_id_not_id(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config()
    session = Session(config, provider=MagicMock(), session_id="child-id", root_id="root-id")

    assert session.get_chainlink_label() == "group:root-id"


def test_set_chainlink_task_defaults_to_none(make_session_config: Callable[..., SessionConfig]) -> None:
    config = make_session_config()
    session = Session(config, provider=MagicMock())

    assert session.cur_chainlink_task_id is None


def test_set_chainlink_task_sets_the_given_id(make_session_config: Callable[..., SessionConfig]) -> None:
    config = make_session_config()
    session = Session(config, provider=MagicMock())

    session.set_chainlink_task(42)

    assert session.cur_chainlink_task_id == 42


def test_set_chainlink_task_clears_a_previously_set_id(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config()
    session = Session(config, provider=MagicMock())
    session.set_chainlink_task(42)

    session.set_chainlink_task(None)

    assert session.cur_chainlink_task_id is None


def test_total_tokens_used_sums_every_messages_client_side_num_tokens(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply("model reply")
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert session.total_tokens_used() == sum(m.num_tokens for m in session.messages)


def test_total_tokens_used_grows_live_as_chunks_stream_in(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    seen_totals: list[int] = []

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        seen_totals.append(session.total_tokens_used())
        assert on_chunk is not None
        on_chunk("hello")
        seen_totals.append(session.total_tokens_used())
        on_chunk(" there, world")
        seen_totals.append(session.total_tokens_used())
        return _reply("hello there, world")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert seen_totals[0] > 0
    assert seen_totals[1] > seen_totals[0]
    assert seen_totals[2] > seen_totals[1]
    assert session.total_tokens_used() == sum(m.num_tokens for m in session.messages)


def test_aborted_placeholder_still_counts_its_partial_content(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def aborting_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        on_chunk("partial rep")
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = aborting_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    with pytest.raises(ResponseAborted):
        session.send_turn("hi")

    _system_message, user_message, assistant_message = session.messages
    assert assistant_message.processing_state == "aborted"
    assert assistant_message.num_tokens == estimate_tokens("partial rep")
    assert user_message.num_tokens == estimate_tokens(user_message.content)
    assert session.total_tokens_used() == sum(m.num_tokens for m in session.messages)


def test_cancel_event_set_mid_turn_aborts_at_the_round_boundary(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """A `cancel_event` that becomes set while a tool-call round is in flight aborts the turn at
    the next round boundary (`_dispatch_turn`) -- the pending tool call is not dispatched and no
    further provider request is made -- rather than waiting for the provider's own mid-stream
    cancel check on a round that may never start. This is what lets an interactive quit or Ctrl+C
    unwind a turn whose worker thread is parked between streams (e.g. on a permission ask). See
    docs/adrs/00120-unblock-worker-thread-before-teardown-so-quit-cannot-hang.md."""
    cancel_event = threading.Event()
    mock_provider = MagicMock()

    def first_round_then_cancel(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        # Simulate the turn being cancelled (quit / Ctrl+C) while this round's reply is assembled.
        assert cancel_event is not None
        cancel_event.set()
        return _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])

    mock_provider.send_prompt.side_effect = first_round_then_cancel
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ResponseAborted):
        session.send_turn("please echo", TurnEventHandlers(cancel_event=cancel_event))

    # Only the first round ran: the boundary check aborted before dispatching the tool call or
    # requesting a second round.
    assert mock_provider.send_prompt.call_count == 1
    assert not any(m.role == "tool_response" for m in session.messages)
    assert session.messages[0].role == "system"
    assert next(m for m in session.messages if m.role == "user").processing_state == "aborted"


def test_total_tokens_used_sums_every_message_across_a_multi_round_turn(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')], num_tokens=3),
        _reply("final answer", num_tokens=4),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    # Each round's reply is a distinct Message, counted exactly once by its own client-side
    # num_tokens -- there's no server-usage delta to double-count against a later round.
    assert session.total_tokens_used() == sum(m.num_tokens for m in session.messages)


def test_send_turn_sends_tool_response_as_wire_formatted_text_but_persists_json(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')], num_tokens=3),
        _reply("final answer", num_tokens=4),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    second_round_messages = mock_provider.send_prompt.call_args_list[1].args[0]
    sent_tool_response = next(m for m in second_round_messages if m.role == "tool_response")
    assert sent_tool_response.content == '"hi"'

    persisted_tool_response = next(m for m in session.messages if m.role == "tool_response")
    assert json.loads(persisted_tool_response.content) == {
        "is_error": False, "is_retryable": False, "response_body": "hi"}


def test_wire_message_snapshot_renders_tool_response_wire_text(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')], num_tokens=3),
        _reply("final answer", num_tokens=4),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    snapshot_tool_response = next(
        m for m in session.wire_message_snapshot() if m.role == "tool_response")
    assert snapshot_tool_response.content == '"hi"'

    persisted_tool_response = next(m for m in session.messages if m.role == "tool_response")
    assert json.loads(persisted_tool_response.content) == {
        "is_error": False, "is_retryable": False, "response_body": "hi"}


def test_successful_tool_call_reflects_compacted_args_for_later_turns(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """A `Tool.update_args()` override's output is stored on the call as `reflected_tool_args`
    and sent to the model on the next round in place of the original `arguments`."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "compacting", '{"keep": "k", "big": "xxxxxxxxxx"}')]),
        _reply("final answer"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("call compacting")

    tool_use_message = next(m for m in session.messages if m.role == "tool_use")
    assert tool_use_message.tool_calls is not None
    call = tool_use_message.tool_calls[0]
    assert call.reflected_tool_args is not None
    assert json.loads(call.reflected_tool_args) == {"keep": "k"}
    assert call.arguments == '{"keep": "k", "big": "xxxxxxxxxx"}'

    # `arguments` itself is left untouched here; the substitution happens only when a real
    # provider builds its outgoing request.
    second_round_messages = mock_provider.send_prompt.call_args_list[1].args[0]
    sent_tool_use = next(m for m in second_round_messages if m.role == "tool_use")
    assert sent_tool_use.tool_calls is not None
    assert sent_tool_use.tool_calls[0] is call


def test_failed_tool_call_leaves_reflected_tool_args_unset(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "compacting", '{"big": "x"}')]),  # missing required "keep"
        _reply("final answer"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("call compacting")

    tool_use_message = next(m for m in session.messages if m.role == "tool_use")
    assert tool_use_message.tool_calls is not None
    assert tool_use_message.tool_calls[0].reflected_tool_args is None


def test_call_interjection_attaches_to_its_own_call_not_just_the_first(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """`Tool.call_interjection()` (e.g. `EditMemoryTool`'s `MEMORY.md`-overflow warning) attaches
    to its own call's envelope even when that call isn't the round's first, unlike a standing
    interjection provider."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([
            ("call_1", "echo", '{"message": "hi"}'),
            ("call_2", "interjecting", '{"message": "hi"}'),
        ]),
        _reply("final answer"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("call echo then interjecting")

    tool_response_messages = [m for m in session.messages if m.role == "tool_response"]
    first_envelope = json.loads(tool_response_messages[0].content)
    second_envelope = json.loads(tool_response_messages[1].content)
    assert "system_interjections" not in first_envelope
    assert second_envelope["system_interjections"] == [
        {"subject": "interjecting", "body": "heads up: hi"}]


def test_total_output_tokens_used_sums_completion_tokens_across_rounds(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')], num_tokens=3, prompt_tokens=10),
        _reply("final answer", num_tokens=4, prompt_tokens=20),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    # Output tokens are the sum of completion tokens for every model round (tool_use + final).
    assert session.total_output_tokens_used() == 3 + 4


def test_total_output_tokens_used_tracks_streaming_estimate(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    seen_outputs: list[int] = []

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        seen_outputs.append(session.total_output_tokens_used())
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_chunk("hello")
        seen_outputs.append(session.total_output_tokens_used())
        on_thinking_chunk("thinking...")
        seen_outputs.append(session.total_output_tokens_used())
        return _reply("hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    # Totals should grow as assistant/thinking content streams in.
    assert seen_outputs[1] > seen_outputs[0]
    assert seen_outputs[2] > seen_outputs[1]
    assistant_message = next(m for m in session.messages if m.role == "assistant")
    thinking_message = next(m for m in session.messages if m.role == "thinking")
    assert (
        session.total_output_tokens_used()
        == assistant_message.num_tokens + thinking_message.num_tokens
    )


def test_max_context_window_reads_registered_model_capabilities(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="alpha")
    registry = sample_model_registry()
    session = Session(config, provider=MagicMock(), model_registry=registry)

    assert session.max_context_window() == 8_000


def test_max_context_window_none_when_model_unregistered(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="some/unregistered-model")
    session = Session(config, provider=MagicMock())

    assert session.max_context_window() is None


def test_active_model_name_falls_back_to_config_model_when_unregistered(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="some/unregistered-model")
    session = Session(config, provider=MagicMock())

    assert session.active_model_name() == "some/unregistered-model"


def test_active_model_name_invokes_registered_model_name(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="alpha")
    registry = sample_model_registry()
    session = Session(config, provider=MagicMock(), model_registry=registry)

    assert session.active_model_name() == "alpha"


def test_send_turn_sends_prompt_to_active_model(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")

    response = session.send_turn("hi")

    assert response == "model reply"
    mock_provider.send_prompt.assert_called_once_with(
        session.messages[:-1], system_prompt=_with_metadata(COMPOSED_OPERATOR_PROMPT,
                               "some/model"), model="some/model",
        session_id="my-session-id", reasoning=None, tools=None, drop_reasoning=False,
        on_chunk=mock.ANY, on_thinking_chunk=mock.ANY, on_reasoning_details=mock.ANY,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None)
    user_msgs_content = [m.content for m in session.messages]
    system_msg = _with_metadata(COMPOSED_OPERATOR_PROMPT, "some/model")
    assert user_msgs_content[0] == system_msg
    assert user_msgs_content[1].endswith("hi")
    assert '<SystemInterjection subject="Metadata">' in user_msgs_content[1]
    assert user_msgs_content[2] == "model reply"


def test_send_turn_attaches_at_mention_fragments_without_altering_prompt_content(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """An `@mention`ed file becomes its own `MessageFragment` on the user `Message`, appended
    ahead of a final fragment wrapping the (unmodified) embellished prompt -- see
    docs/specs/at-mention-file-inlining.md. `content` itself keeps the plain prompt text."""
    (tmp_path / "notes.txt").write_text("line one\n")
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")

    session.send_turn("check @notes.txt please")

    user_message = session.messages[1]
    assert user_message.content.endswith("check @notes.txt please")
    assert "@notes.txt" in user_message.content
    assert user_message.fragments is not None
    assert len(user_message.fragments) == 2
    assert "Filename: notes.txt" in user_message.fragments[0].text
    assert "1|line one" in user_message.fragments[0].text
    assert user_message.fragments[1].text == user_message.content


def test_send_turn_attaches_at_mentioned_image_as_image_fragment(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """An @mentioned file recognized as an image (see klorb.session.mixins.mentions.
    detect_mention_mime_type) is resized/transcoded and attached as an image_url fragment when
    the active model supports vision -- the same pipeline a drag-drop/paste attachment goes
    through. See docs/specs/at-mention-file-inlining.md and docs/specs/vision-image-input.md."""
    buffer = io.BytesIO()
    Image.new("RGB", (20, 10), (10, 20, 30)).save(buffer, format="PNG")
    (tmp_path / "shot.png").write_bytes(buffer.getvalue())

    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=NO_SUCH_DIR)
    registry.register(ConfiguredModel(
        {"name": "vision/model", "capabilities": {"vision": True}}, source="test"))

    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="vision/model", workspace=Workspace(path=tmp_path))
    session = Session(
        config, provider=mock_provider, model_registry=registry, session_id="my-session-id")

    session.send_turn("check @shot.png please")

    user_message = session.messages[1]
    assert user_message.fragments is not None
    assert [f.type for f in user_message.fragments] == ["text", "image_url", "text"]
    assert "Filename: shot.png" in user_message.fragments[0].text
    image_fragment = user_message.fragments[1]
    assert image_fragment.image_url is not None
    assert image_fragment.source_filename == "shot.png"


def test_send_turn_leaves_fragments_none_without_at_mentions(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")

    session.send_turn("hi")

    assert session.messages[1].fragments is None


def test_send_turn_appends_image_fragments_after_prompt_with_headers(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """Image fragments are appended after the prompt's own text fragment (vendor guidance:
    send text first, then images), each preceded by a header fragment naming its position and
    origin -- see _image_header_text and docs/specs/vision-image-input.md."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")
    image_fragment = MessageFragment(
        type="image_url", image_url={"url": "data:image/png;base64,xx"}, mime_type="image/png",
        source_filename="shot.png")

    session.send_turn("what is this?", image_fragments=[image_fragment])

    user_message = session.messages[1]
    assert user_message.fragments is not None
    assert [f.type for f in user_message.fragments] == ["text", "text", "image_url"]
    assert user_message.fragments[0].text == user_message.content
    assert "image #1" in user_message.fragments[1].text
    assert "filename='shot.png'" in user_message.fragments[1].text
    assert user_message.fragments[2] is image_fragment


def test_send_turn_image_header_notes_clipboard_paste_when_no_filename(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")
    image_fragment = MessageFragment(
        type="image_url", image_url={"url": "data:image/png;base64,xx"}, mime_type="image/png")

    session.send_turn("what is this?", image_fragments=[image_fragment])

    fragments = session.messages[1].fragments
    assert fragments is not None
    assert "pasted from clipboard" in fragments[1].text


def test_run_one_shot_delegates_to_send_turn(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")

    response = session.run_one_shot("hi")

    assert response == "model reply"
    mock_provider.send_prompt.assert_called_once_with(
        session.messages[:-1], system_prompt=_with_metadata(COMPOSED_OPERATOR_PROMPT,
                               "some/model"), model="some/model",
        session_id="my-session-id", reasoning=None, tools=None, drop_reasoning=False,
        on_chunk=mock.ANY, on_thinking_chunk=mock.ANY, on_reasoning_details=mock.ANY,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None)


# --- session naming: send_turn()'s first-call naming trigger ---


def test_session_naming_pending_defaults_true_without_a_session_name(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock())

    assert session.session_naming_pending is True


def test_session_naming_pending_false_when_session_name_already_given(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock(), session_name="Already Named")

    assert session.session_naming_pending is False


def test_send_turn_runs_naming_classifier_on_first_call_and_sets_title_not_id(
    monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    """Session naming runs on a background thread (`SessionCoreMixin._start_session_naming`), so
    the test synchronizes on `on_session_name_changed` firing rather than racing it."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(), provider=mock_provider, session_id="2026-07-21-18-10-old-nonce")
    monkeypatch.setattr(
        "klorb.session.mixins.core.generate_session_name",
        lambda *args, **kwargs: SessionName(title="Fix auth bug"))
    done = threading.Event()
    callback = MagicMock(side_effect=lambda result: done.set())

    session.send_turn("please fix the auth bug", TurnEventHandlers(on_session_name_changed=callback))

    assert done.wait(timeout=2.0), "session naming classifier did not complete in time"
    assert session.id == "2026-07-21-18-10-old-nonce"
    assert session.root_id == "2026-07-21-18-10-old-nonce"
    assert session.name == "Fix auth bug"
    assert session.session_naming_pending is False


def test_send_turn_naming_failure_sets_fallback_title(
    monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(), provider=mock_provider, session_id="2026-07-21-18-10-old-nonce")
    monkeypatch.setattr(
        "klorb.session.mixins.core.generate_session_name", lambda *args, **kwargs: None)
    done = threading.Event()
    callback = MagicMock(side_effect=lambda result: done.set())

    session.send_turn("hi", TurnEventHandlers(on_session_name_changed=callback))

    assert done.wait(timeout=2.0), "session naming classifier did not complete in time"
    assert session.id == "2026-07-21-18-10-old-nonce"
    assert session.name == "hi..."
    assert session.session_naming_pending is False


def test_send_turn_does_not_retrigger_naming_on_second_call(
    monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(), provider=mock_provider, session_id="2026-07-21-18-10-old-nonce")
    done = threading.Event()
    naming_spy = MagicMock(side_effect=lambda *args, **kwargs: done.set())
    monkeypatch.setattr("klorb.session.mixins.core.generate_session_name", naming_spy)

    session.send_turn("first")
    assert done.wait(timeout=2.0), "session naming classifier did not complete in time"
    session.send_turn("second")

    naming_spy.assert_called_once()


def test_send_turn_skips_naming_when_session_name_already_given(
    monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(
        make_session_config(), provider=mock_provider, session_id="2026-07-21-18-10-old-nonce",
        session_name="Already Named")
    naming_spy = MagicMock(return_value=None)
    monkeypatch.setattr("klorb.session.mixins.core.generate_session_name", naming_spy)

    session.send_turn("hi")

    naming_spy.assert_not_called()
    assert session.id == "2026-07-21-18-10-old-nonce"


def test_send_turn_does_not_block_on_a_slow_naming_classifier(
    monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    """The classifier runs on its own background thread (`_start_session_naming`), so a slow
    classifier round trip must not delay the turn's own dispatch/response."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(), provider=mock_provider, session_id="2026-07-21-18-10-old-nonce")
    classifier_started = threading.Event()
    release_classifier = threading.Event()

    def slow_generate_session_name(*args: Any, **kwargs: Any) -> SessionName:
        classifier_started.set()
        release_classifier.wait(timeout=2.0)
        return SessionName(title="Fix auth bug")

    monkeypatch.setattr(
        "klorb.session.mixins.core.generate_session_name", slow_generate_session_name)

    started = time.perf_counter()
    session.send_turn("please fix the auth bug")
    elapsed = time.perf_counter() - started

    assert classifier_started.wait(timeout=1.0), "classifier should have started"
    assert elapsed < 1.0, "send_turn should not block on the naming classifier"
    release_classifier.set()


def test_send_turn_invokes_on_session_name_changed_callback(
    monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(), provider=mock_provider, session_id="2026-07-21-18-10-old-nonce")
    name = SessionName(title="Fix auth bug")
    monkeypatch.setattr(
        "klorb.session.mixins.core.generate_session_name", lambda *args, **kwargs: name)
    done = threading.Event()
    spy = MagicMock(side_effect=lambda result: done.set())

    session.send_turn("please fix the auth bug", TurnEventHandlers(on_session_name_changed=spy))

    assert done.wait(timeout=2.0), "session naming classifier did not complete in time"
    spy.assert_called_once_with(name)


def test_send_turn_passes_system_prompt_from_registered_model(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="alpha", role_name=ROLE_WITHOUT_PROMPT_FILES)
    registry = sample_model_registry()
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["system_prompt"] == _with_metadata(
        "You are Alpha.", "alpha", knowledge_cutoff="2024-01-01")


def test_role_prompt_layers_onto_registered_model_prompt(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="alpha")  # role_name defaults to "operator"
    registry = sample_model_registry()
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["system_prompt"] == _with_metadata(
        f"You are Alpha.\n\n<AgentRole>\n{OPERATOR_PROMPT}\n</AgentRole>",
        "alpha", knowledge_cutoff="2024-01-01")


def test_unknown_role_on_unregistered_model_falls_back_to_default_prompt(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/unregistered-model", role_name=ROLE_WITHOUT_PROMPT_FILES)
    session = Session(config, provider=mock_provider)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["system_prompt"] == _with_metadata(
        DEFAULT_PROMPT or "", "some/unregistered-model")


def test_system_message_inserted_before_first_turn_for_registered_model(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="alpha", role_name=ROLE_WITHOUT_PROMPT_FILES)
    registry = sample_model_registry()
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    assert [m.role for m in session.messages] == ["system", "user", "assistant"]
    assert session.messages[0].content == _with_metadata(
        "You are Alpha.", "alpha", knowledge_cutoff="2024-01-01")


def test_system_message_not_duplicated_across_turns(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("r1"), _reply("r2")]
    config = make_session_config(model="alpha")
    registry = sample_model_registry()
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("first")
    session.send_turn("second")

    assert sum(1 for m in session.messages if m.role == "system") == 1


def test_system_message_holds_role_prompt_when_model_unregistered(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(model="some/unregistered-model"), provider=mock_provider)

    session.send_turn("hi")

    assert session.messages[0].role == "system"
    assert session.messages[0].content == _with_metadata(
        COMPOSED_OPERATOR_PROMPT, "some/unregistered-model")


def test_system_message_inserted_ahead_of_tool_defs_message(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="alpha")
    registry = sample_model_registry()
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider,
                      model_registry=registry, tool_registry=tool_registry)

    session.send_turn("hi")

    assert [m.role for m in session.messages] == ["system", "tool_defs", "user", "assistant"]


def test_reasoning_defaults_to_high_effort_for_effort_style_thinking_model(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = sample_model_registry()
    session = Session(make_session_config(model="beta"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"effort": "high"}


def test_reasoning_respects_configured_effort_level(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = sample_model_registry()
    config = make_session_config(model="beta", thinking_effort="low")
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"effort": "low"}


def test_reasoning_uses_token_budget_for_tokens_style_thinking_model(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = sample_model_registry()
    session = Session(make_session_config(model="gamma"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"max_tokens": THINKING_EFFORT_TOKEN_BUDGETS["high"]}


def test_reasoning_uses_custom_token_budgets_when_given(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = sample_model_registry()
    custom_budgets: dict[ThinkingEffort, int] = {"low": 1_000, "medium": 2_000, "high": 3_000}
    session = Session(
        make_session_config(model="gamma"), provider=mock_provider, model_registry=registry,
        process_config=ProcessConfig(thinking_token_budgets=custom_budgets))

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"max_tokens": 3_000}
    assert session.thinking_token_budgets == custom_budgets


def test_reasoning_none_when_thinking_disabled(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = sample_model_registry()
    config = make_session_config(model="beta", thinking_enabled=False)
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


def test_reasoning_none_when_model_does_not_support_thinking(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = sample_model_registry()
    session = Session(make_session_config(model="alpha"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


def test_reasoning_none_when_model_unregistered(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(model="some/unregistered-model"), provider=mock_provider)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


class _DropReasoningModel(Model):
    """A registered `Model` test double declaring `drop_reasoning() -> True`, so `Session`
    tests can verify that flag reaches `ApiProvider.send_prompt()`. See
    `fixtures.sample_models`'s `AlphaModel`/`BetaModel`/`GammaModel` for the same pattern used
    for `thinking`/`thinking_budget_style` coverage."""

    def name(self) -> str:
        return "drops-reasoning"

    def settings(self) -> dict[str, Any]:
        return {}

    def capabilities(self) -> dict[str, Any]:
        return {}

    def drop_reasoning(self) -> bool:
        return True


def test_drop_reasoning_passed_to_provider_when_active_model_declares_it(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=NO_SUCH_DIR)
    registry.register(_DropReasoningModel())
    session = Session(
        make_session_config(model="drops-reasoning"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["drop_reasoning"] is True


def test_drop_reasoning_false_by_default_for_registered_model(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = sample_model_registry()
    session = Session(make_session_config(model="alpha"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["drop_reasoning"] is False


def test_drop_reasoning_false_when_model_unregistered(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(model="some/unregistered-model"), provider=mock_provider)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["drop_reasoning"] is False


def test_total_tokens_used_excludes_thinking_when_drop_reasoning_is_true(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_thinking_chunk("thinking...")
        on_chunk("hello")
        return _reply("hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    registry = ModelRegistry(packaged_models_dir=NO_SUCH_DIR, user_models_dir=NO_SUCH_DIR)
    registry.register(_DropReasoningModel())
    session = Session(
        make_session_config(model="drops-reasoning"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    thinking_message = next(m for m in session.messages if m.role == "thinking")
    assert session.total_tokens_used() == sum(
        m.num_tokens for m in session.messages if m is not thinking_message)


def test_total_tokens_used_includes_thinking_by_default(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_thinking_chunk("thinking...")
        on_chunk("hello")
        return _reply("hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert session.total_tokens_used() == sum(m.num_tokens for m in session.messages)


def test_send_turn_sends_full_history_to_provider(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("r1", num_tokens=5, prompt_tokens=10), _reply("r2")]
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("first")
    session.send_turn("second")

    second_call_messages = mock_provider.send_prompt.call_args_list[1].args[0]
    assert second_call_messages[0].content == _with_metadata(
        COMPOSED_OPERATOR_PROMPT, "some/model")
    assert second_call_messages[1].content.endswith("first")
    assert second_call_messages[2].content == "r1"
    assert second_call_messages[3].content == "second"


def test_user_message_num_tokens_is_its_own_client_side_estimate(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("r1"), _reply("r2")]
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("first")
    session.send_turn("second")

    system_message, user1, assistant1, user2, assistant2 = session.messages
    assert system_message.num_tokens == estimate_tokens(
        _with_metadata(COMPOSED_OPERATOR_PROMPT, "some/model"))
    assert user1.num_tokens == estimate_tokens(user1.content)
    assert user2.num_tokens == estimate_tokens(user2.content)


def test_send_turn_marks_user_message_error_and_reraises_on_provider_failure(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = RuntimeError("boom")
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    failed_message = session.messages[-1]
    assert failed_message.processing_state == "error"
    assert failed_message.last_error == "boom"


def test_retry_last_turn_mutates_same_message_on_success(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [RuntimeError("boom"), _reply("recovered")]
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    response = session.retry_last_turn()

    assert response == "recovered"
    assert len(session.messages) == 3
    user_message = session.messages[1]
    assert user_message.content.endswith("hi")
    assert "<SystemInterjection" in user_message.content
    assert user_message.processing_state == "complete"
    assert user_message.last_error is None


def test_retry_last_turn_raises_when_nothing_errored(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(model="some/model"), provider=MagicMock())

    with pytest.raises(ValueError, match="No errored turn to retry."):
        session.retry_last_turn()


def test_streaming_chunks_populate_and_finalize_placeholder_message(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_chunk("Hel")
        on_chunk("lo")
        return _reply("Hello", num_tokens=2, prompt_tokens=10)

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert len(session.messages) == 3
    assistant_message = session.messages[-1]
    assert assistant_message.content == "Hello"
    assert assistant_message.streaming_content is None
    assert assistant_message.processing_state == "complete"
    assert assistant_message.num_tokens == 2


def test_send_turn_forwards_chunks_to_caller_on_chunk(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_chunk("Hel")
        on_chunk("lo")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)
    spy = MagicMock()

    session.send_turn("hi", TurnEventHandlers(on_chunk=spy))

    assert [call.args[0] for call in spy.call_args_list] == ["Hel", "lo"]


def test_streaming_thinking_chunks_populate_and_finalize_a_separate_placeholder_message(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_thinking_chunk("Let ")
        on_thinking_chunk("me think.")
        on_chunk("Hello")
        return _reply("Hello", num_tokens=2, prompt_tokens=10)

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert len(session.messages) == 4
    system_message, user_message, thinking_message, assistant_message = session.messages
    assert system_message.role == "system"
    assert user_message.role == "user"
    assert thinking_message.role == "thinking"
    assert thinking_message.content == "Let me think."
    assert thinking_message.streaming_content is None
    assert thinking_message.processing_state == "complete"
    assert thinking_message.num_tokens == estimate_tokens("Let me think.")
    assert assistant_message.role == "assistant"
    assert assistant_message.content == "Hello"


def test_send_turn_forwards_thinking_chunks_to_caller_on_thinking_chunk(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_thinking_chunk("Let ")
        on_thinking_chunk("me think.")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)
    spy = MagicMock()

    session.send_turn("hi", TurnEventHandlers(on_thinking_chunk=spy))

    assert [call.args[0] for call in spy.call_args_list] == ["Let ", "me think."]


def test_mid_stream_failure_marks_user_and_partial_assistant_message_error(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        on_chunk("partial")
        raise RuntimeError("boom")

    mock_provider.send_prompt.side_effect = failing_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    _system_message, user_message, assistant_message = session.messages
    assert user_message.processing_state == "error"
    assert user_message.last_error == "boom"
    assert assistant_message.processing_state == "error"
    assert assistant_message.last_error == "boom"
    assert assistant_message.streaming_content == ["partial"]


def test_mid_stream_failure_marks_thinking_placeholder_error_too(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_thinking_chunk is not None
        on_thinking_chunk("partial thought")
        raise RuntimeError("boom")

    mock_provider.send_prompt.side_effect = failing_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    _system_message, user_message, thinking_message = session.messages
    assert user_message.processing_state == "error"
    assert thinking_message.processing_state == "error"
    assert thinking_message.last_error == "boom"
    assert thinking_message.streaming_content == ["partial thought"]


def test_abort_before_any_chunk_marks_user_message_aborted_not_removed(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = ResponseAborted()
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    with pytest.raises(ResponseAborted):
        session.send_turn("hi")

    _system_message, user_message = session.messages
    assert user_message.processing_state == "aborted"
    assert user_message.last_error is None
    assert user_message.num_tokens == estimate_tokens(
        user_message.content)


def test_abort_mid_stream_keeps_partial_assistant_and_thinking_content(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def aborting_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        assert on_thinking_chunk is not None
        on_thinking_chunk("thinking out loud")
        on_chunk("partial rep")
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = aborting_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    with pytest.raises(ResponseAborted):
        session.send_turn("hi")

    _system_message, user_message, thinking_message, assistant_message = session.messages
    assert user_message.processing_state == "aborted"
    assert thinking_message.processing_state == "aborted"
    assert thinking_message.content == "thinking out loud"
    assert thinking_message.streaming_content is None
    assert assistant_message.processing_state == "aborted"
    assert assistant_message.content == "partial rep"
    assert assistant_message.streaming_content is None


def test_abort_after_a_completed_tool_call_round_keeps_that_rounds_messages(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    calls_made = 0

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        nonlocal calls_made
        calls_made += 1
        if calls_made == 1:
            return _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ResponseAborted):
        session.send_turn("please echo")

    roles = [m.role for m in session.messages]
    assert roles == ["system", "tool_defs", "user", "tool_use", "tool_response"]
    tool_use_message = session.messages[3]
    assert tool_use_message.tool_calls == [
        ToolCallRequest(id="call_1", name="echo", arguments='{"message": "hi"}')]
    tool_response_message = session.messages[4]
    assert isinstance(tool_response_message.content, str)
    envelope = json.loads(tool_response_message.content)
    assert envelope["is_error"] is False
    assert envelope["response_body"] == "hi"
    user_message = session.messages[2]
    assert user_message.processing_state == "aborted"
    # The user message's own num_tokens is set once, from its own content, at construction --
    # unaffected by how many rounds ran, or whether the last one was aborted.
    assert user_message.num_tokens == estimate_tokens(user_message.content)


def test_retry_last_turn_discards_partial_assistant_fragment_and_recovers(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None, max_tokens=None,
    ):
        assert on_chunk is not None
        on_chunk("partial")
        raise RuntimeError("boom")

    mock_provider.send_prompt.side_effect = failing_send_prompt
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    assert len(session.messages) == 3

    mock_provider.send_prompt.side_effect = None
    mock_provider.send_prompt.return_value = _reply("recovered")

    response = session.retry_last_turn()

    assert response == "recovered"
    assert len(session.messages) == 3
    assert session.messages[1].processing_state == "complete"
    assert session.messages[2].content == "recovered"


def test_no_tools_offered_when_tool_registry_unset(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["tools"] is None


def test_tool_definitions_offered_to_provider_when_tool_registry_set(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert {d["function"]["name"] for d in kwargs["tools"]} == {
        "echo", "add", "ask_permission", "ask_multi_permission", "compacting", "interjecting"}


def test_tool_defs_message_inserted_before_first_turn(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("hi")

    assert session.messages[1].role == "tool_defs"
    assert [m.role for m in session.messages] == ["system", "tool_defs", "user", "assistant"]


def test_tool_defs_message_not_duplicated_across_turns(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("r1"), _reply("r2")]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("first")
    session.send_turn("second")

    assert sum(1 for m in session.messages if m.role == "tool_defs") == 1


def test_no_tool_defs_message_when_tool_registry_unset(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(make_session_config(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert all(m.role != "tool_defs" for m in session.messages)


def test_tool_call_round_trip_dispatches_tool_and_returns_final_reply(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi there"}')]),
        _reply("final answer", num_tokens=4, prompt_tokens=20),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response = session.send_turn("please echo")

    assert response == "final answer"
    roles = [m.role for m in session.messages]
    assert roles == ["system", "tool_defs", "user", "tool_use", "tool_response", "assistant"]
    tool_use_message = session.messages[3]
    assert tool_use_message.tool_calls == [
        ToolCallRequest(id="call_1", name="echo", arguments='{"message": "hi there"}')]
    tool_response_message = session.messages[4]
    assert tool_response_message.tool_call_id == "call_1"
    assert isinstance(tool_response_message.content, str)
    envelope = json.loads(tool_response_message.content)
    assert envelope["is_error"] is False
    assert envelope["response_body"] == "hi there"
    user_message = session.messages[2]
    assert user_message.processing_state == "complete"
    assert user_message.num_tokens == estimate_tokens(
        user_message.content)
    assert mock_provider.send_prompt.call_count == 2


def test_tool_call_round_trip_forwards_tool_calls_to_second_request(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    second_call_messages = mock_provider.send_prompt.call_args_list[1].args[0]
    assert [m.role for m in second_call_messages] == [
        "system", "tool_defs", "user", "tool_use", "tool_response"]


def test_unknown_tool_call_reports_error_to_model(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "NoSuchTool", "{}")]),
        _reply("recovered"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response = session.send_turn("call a bogus tool")

    assert response == "recovered"
    tool_response_message = session.messages[4]
    assert tool_response_message.role == "tool_response"
    assert isinstance(tool_response_message.content, str)
    envelope = json.loads(tool_response_message.content)
    assert envelope["is_error"] is True
    assert envelope["error_category"] == "validation"


def test_malformed_json_tool_call_reports_error_to_model_instead_of_raising(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"')]),  # missing closing brace
        _reply("recovered"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response = session.send_turn("call a tool with malformed arguments")

    assert response == "recovered"
    tool_response_message = session.messages[4]
    assert tool_response_message.role == "tool_response"
    assert isinstance(tool_response_message.content, str)
    envelope = json.loads(tool_response_message.content)
    assert envelope["is_error"] is True
    assert envelope["error_category"] == "syntax"
    assert "Invalid JSON" in envelope["error_message"]
    user_message = session.messages[2]
    assert user_message.processing_state == "complete"


def test_malformed_json_tool_call_removed_from_tool_use_message(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """A tool call with invalid JSON arguments is has its arguments reset to `{}`
    so the malformed data doesn't get sent to the API on subsequent turns (which would
    cause a 400 Bad Request error due to the invalid JSON)."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"')]),  # missing closing brace
        _reply("recovered"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response = session.send_turn("call a tool with malformed arguments")

    assert response == "recovered"
    # The tool_use message should have the invalid tool call removed from tool_calls
    tool_use_message = session.messages[3]
    assert tool_use_message.role == "tool_use"
    assert tool_use_message.tool_calls is not None
    assert len(tool_use_message.tool_calls) == 1
    assert tool_use_message.tool_calls[0].arguments == "{}"


def test_malformed_json_among_valid_tool_calls_removes_only_the_bad_one(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    """When a batch of tool calls contains a mix of valid and invalid JSON, only the
    invalid one is removed from the tool_use message's tool_calls list."""
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([
            ("call_1", "echo", '{"message": "valid"}'),
            ("call_2", "echo", '{"message": "bad"'),  # missing closing brace
        ]),
        _reply("recovered"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response = session.send_turn("call tools with one malformed")

    assert response == "recovered"
    tool_use_message = session.messages[3]
    assert tool_use_message.role == "tool_use"
    assert tool_use_message.tool_calls is not None
    assert len(tool_use_message.tool_calls) == 2
    assert tool_use_message.tool_calls[0].id == "call_1"
    assert tool_use_message.tool_calls[0].arguments == '{"message": "valid"}'
    # Two tool_response messages: one for the valid call, one for the malformed call
    assert tool_use_message.tool_calls[1].id == "call_2"
    assert tool_use_message.tool_calls[1].arguments == '{}' # replaced
    tool_response_messages = [
        m for m in session.messages if m.role == "tool_response"
    ]
    assert len(tool_response_messages) == 2


def test_on_tool_call_fires_with_raw_arguments_for_malformed_json(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"')]),
        _reply("recovered"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)
    events: list[ToolCallEvent] = []

    session.send_turn("call a tool with malformed arguments", TurnEventHandlers(
        on_tool_call=events.append))

    assert len(events) == 1
    assert events[0].name == "echo"
    assert events[0].args == {}
    assert events[0].raw_arguments == '{"message": "hi"'
    assert events[0].error is not None
    assert "Invalid JSON" in events[0].error


def test_round_limit_exceeded_raises_and_marks_user_message_error(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = make_session_config(model="some/model", max_tool_calls_per_turn=1_000)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ToolCallLimitExceeded):
        session.send_turn("loop forever")

    assert mock_provider.send_prompt.call_count == MAX_TOOL_CALL_ROUNDS + 1
    user_message = session.messages[2]
    assert user_message.processing_state == "error"
    assert str(MAX_TOOL_CALL_ROUNDS) in (user_message.last_error or "")


def test_per_turn_tool_call_limit_defaults(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ToolCallLimitExceeded, match="100 tool call"):
        session.send_turn("loop forever")

    tool_response_messages = [m for m in session.messages if m.role == "tool_response"]
    assert len(tool_response_messages) == DEFAULT_MAX_TOOL_CALLS_PER_TURN
    user_message = session.messages[2]
    assert user_message.processing_state == "error"


def test_per_turn_tool_call_limit_is_configurable(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = make_session_config(model="some/model", max_tool_calls_per_turn=2)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ToolCallLimitExceeded, match="2 tool call"):
        session.send_turn("loop forever")

    tool_response_messages = [m for m in session.messages if m.role == "tool_response"]
    assert len(tool_response_messages) == 2


def test_per_turn_tool_call_limit_resets_between_turns(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _reply("first done"),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
        _reply("second done"),
    ]
    config = make_session_config(model="some/model", max_tool_calls_per_turn=1)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response1 = session.send_turn("first")
    response2 = session.send_turn("second")

    assert response1 == "first done"
    assert response2 == "second done"


def test_approving_turn_limit_increase_doubles_it_and_continues(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
        _tool_call_reply([("call_3", "echo", '{"message": "c"}')]),
        _reply("finally done"),
    ]
    config = make_session_config(model="some/model", max_tool_calls_per_turn=1)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)
    on_limit_reached = MagicMock(return_value=True)

    response = session.send_turn(
        "loop a few times", TurnEventHandlers(on_tool_call_limit_reached=on_limit_reached))

    assert response == "finally done"
    tool_response_messages = [m for m in session.messages if m.role == "tool_response"]
    assert len(tool_response_messages) == 3
    assert on_limit_reached.call_count == 2
    assert config.max_tool_calls_per_turn == 4  # 1 -> 2 -> 4
    assert "reaching its configured limit" in on_limit_reached.call_args_list[0].args[0]


def test_declining_turn_limit_increase_raises(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = make_session_config(model="some/model", max_tool_calls_per_turn=1)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)
    on_limit_reached = MagicMock(return_value=False)

    with pytest.raises(ToolCallLimitExceeded, match="1 tool call"):
        session.send_turn("loop forever", TurnEventHandlers(on_tool_call_limit_reached=on_limit_reached))

    on_limit_reached.assert_called_once()
    assert config.max_tool_calls_per_turn == 1  # unchanged


def test_no_callback_declines_without_asking(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = make_session_config(model="some/model", max_tool_calls_per_turn=1)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ToolCallLimitExceeded, match="1 tool call"):
        session.send_turn("loop forever")

    assert config.max_tool_calls_per_turn == 1  # unchanged


# --- on_tool_call ---


def test_on_tool_call_fires_once_per_successful_call(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi there"}')]),
        _reply("final answer"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)
    on_tool_call = MagicMock()

    session.send_turn("please echo", TurnEventHandlers(on_tool_call=on_tool_call))

    on_tool_call.assert_called_once()
    (event,), _ = on_tool_call.call_args
    assert event.call_id == "call_1"
    assert event.name == "echo"
    assert event.args == {"message": "hi there"}
    assert event.result == "hi there"
    assert event.error is None


def test_on_tool_call_fires_with_error_for_unknown_tool_name(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "NoSuchTool", "{}")]),
        _reply("recovered"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)
    on_tool_call = MagicMock()

    session.send_turn("call a bogus tool", TurnEventHandlers(on_tool_call=on_tool_call))

    on_tool_call.assert_called_once()
    (event,), _ = on_tool_call.call_args
    assert event.name == "NoSuchTool"
    assert event.result is None
    assert event.error is not None


def test_log_tool_calls_disabled_by_default_writes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    assert not (tmp_path / "tool-calls.log").exists()


def test_log_tool_calls_enabled_via_process_config_writes_request_and_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi there"}')]),
        _reply("final answer"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(
        config, provider=mock_provider, tool_registry=tool_registry,
        process_config=ProcessConfig(log_tool_calls=True),
    )

    session.send_turn("please echo")

    log_path = tmp_path / "tool-calls.log"
    assert log_path.exists()
    contents = log_path.read_text(encoding="utf-8")
    assert contents.startswith("---\n")
    assert "Request:" in contents
    assert '"name": "echo"' in contents
    assert '"message": "hi there"' in contents
    assert "Response:" in contents
    assert '"result": "hi there"' in contents


def test_log_tool_calls_separates_entries_with_a_blank_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _reply("first done"),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
        _reply("second done"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(
        config, provider=mock_provider, tool_registry=tool_registry,
        process_config=ProcessConfig(log_tool_calls=True),
    )

    session.send_turn("first")
    session.send_turn("second")

    contents = (tmp_path / "tool-calls.log").read_text(encoding="utf-8")
    assert contents.count("---") == 2
    assert "\n\n---\n" in contents


def test_log_tool_calls_enabled_via_env_var_with_no_process_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    monkeypatch.setenv("LOG_TOOL_CALLS", "true")
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    config = make_session_config(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    assert (tmp_path / "tool-calls.log").exists()


def test_on_tool_call_fires_with_retried_result_after_permission_grant(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)
    on_tool_call = MagicMock()
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    session.send_turn("try it", TurnEventHandlers(
        on_tool_call=on_tool_call, on_permission_ask=on_permission_ask))

    on_tool_call.assert_called_once()
    (event,), _ = on_tool_call.call_args
    assert event.result == f"granted:{target}"
    assert event.error is None


def test_on_tool_call_fires_with_error_for_a_denied_permission_ask(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)
    on_tool_call = MagicMock()
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="deny"))

    session.send_turn("try it", TurnEventHandlers(
        on_tool_call=on_tool_call, on_permission_ask=on_permission_ask))

    on_tool_call.assert_called_once()
    (event,), _ = on_tool_call.call_args
    assert event.result is None
    assert event.error is not None


# --- on_permission_ask ---


def _ask_permission_call(id_: str, path: Path, *, is_write: bool = True) -> tuple[str, str, str]:
    return id_, "ask_permission", json.dumps({"path": str(path), "is_write": is_write})


def _session_with_ask_tool(
    config: SessionConfig, mock_provider: MagicMock, process_config: ProcessConfig | None = None,
) -> Session:
    tool_registry = ToolRegistry.discover_tools(
        process_config or ProcessConfig(), config, package=sample_tools_package)
    return Session(
        config, provider=mock_provider, tool_registry=tool_registry, process_config=process_config)


def _tool_response_envelope(session: Session) -> dict[str, Any]:
    tool_response = next(m for m in session.messages if m.role == "tool_response")
    assert isinstance(tool_response.content, str)
    envelope: dict[str, Any] = json.loads(tool_response.content)
    return envelope


def _tool_response_content(session: Session) -> str:
    """The tool_response envelope rendered back to the old `_format_tool_response_content()`
    shape (`"Error: {message}"` on failure, the raw result body on success) -- lets most of
    this module's pre-envelope substring/equality assertions against the ask/permission tools'
    plain-string results keep working unchanged."""
    envelope = _tool_response_envelope(session)
    if envelope["is_error"]:
        return f"Error: {envelope['error_message']}"
    body = envelope["response_body"]
    return body if isinstance(body, str) else json.dumps(body)


def test_permission_ask_headless_fails_closed_like_a_generic_error(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)

    response = session.send_turn("try it")

    assert response == "done"
    assert _tool_response_content(session) == f"Error: Permission requires confirmation: access {target}"


def test_set_permission_framework_updates_config(make_session_config: Callable[..., SessionConfig]) -> None:
    config = make_session_config(model="some/model")
    session = Session(config, provider=MagicMock())

    session.set_permission_framework("auto")

    assert session.config.permission_framework == "auto"


def test_set_permission_framework_rejects_an_invalid_value(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="some/model")
    session = Session(config, provider=MagicMock())

    with pytest.raises(ValueError, match="not-a-real-mode"):
        session.set_permission_framework("not-a-real-mode")  # type: ignore[arg-type]

    assert session.config.permission_framework == "ask"
    assert session._pending_permission_framework_interjection is None


def test_set_permission_framework_queues_interjection_prepended_to_next_turn(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider)

    session.set_permission_framework("auto")
    session.send_turn("do the thing")

    expected = (
        '<SystemInterjection subject="PermissionFramework">\n'
        f"{PERMISSION_FRAMEWORK_INTERJECTIONS['auto']}\n"
        "</SystemInterjection>\n"
        "do the thing"
    )
    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[0].content.endswith(expected.split("\n")[-1])


def test_send_turn_with_no_pending_change_leaves_prompt_untouched(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider)

    session.send_turn("do the thing")

    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[0].content.endswith("do the thing")


def test_multiple_permission_framework_changes_collapse_to_final_interjection(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider)

    session.set_permission_framework("auto")
    session.set_permission_framework("deny")
    session.set_permission_framework("ask")
    session.send_turn("do the thing")

    expected = (
        '<SystemInterjection subject="PermissionFramework">\n'
        f"{PERMISSION_FRAMEWORK_INTERJECTIONS['ask']}\n"
        "</SystemInterjection>\n"
        "do the thing"
    )
    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[0].content.endswith(expected.split("\n")[-1])


def test_pending_interjection_applied_exactly_once(make_session_config: Callable[..., SessionConfig]) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("first"), _reply("second")]
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider)

    session.set_permission_framework("auto")
    session.send_turn("first turn")
    session.send_turn("second turn")

    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[1].content.endswith("second turn")


def test_standing_interjection_appears_while_provider_returns_a_message(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("first"), _reply("second")]
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider)

    session.register_standing_interjection("SessionTerminal", lambda: "still open")
    session.send_turn("first turn")
    session.send_turn("second turn")

    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[0].content.endswith("first turn")
    assert user_messages[1].content.endswith("second turn")


def test_standing_interjection_stops_once_provider_returns_none(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("first"), _reply("second")]
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider)

    live = {"alive": True}
    session.register_standing_interjection(
        "SessionTerminal", lambda: "still open" if live["alive"] else None)
    session.send_turn("first turn")
    live["alive"] = False
    session.send_turn("second turn")

    user_messages = [m for m in session.messages if m.role == "user"]
    assert "SessionTerminal" in user_messages[0].content
    assert user_messages[1].content.endswith("second turn")


def test_reregistering_standing_interjection_overwrites_not_accumulates(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider)

    session.register_standing_interjection("SessionTerminal", lambda: "first version")
    session.register_standing_interjection("SessionTerminal", lambda: "second version")
    session.send_turn("do the thing")

    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[0].content.count("SystemInterjection") >= 2
    assert "second version" in user_messages[0].content
    assert "first version" not in user_messages[0].content


def test_standing_interjection_coexists_with_one_shot_permission_framework_interjection(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider)

    session.set_permission_framework("auto")
    session.register_standing_interjection("SessionTerminal", lambda: "still open")
    session.send_turn("do the thing")

    expected = (
        '<SystemInterjection subject="SessionTerminal">\n'
        "still open\n"
        "</SystemInterjection>\n"
        '<SystemInterjection subject="PermissionFramework">\n'
        f"{PERMISSION_FRAMEWORK_INTERJECTIONS['auto']}\n"
        "</SystemInterjection>\n"
        "do the thing"
    )
    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[0].content.endswith(expected.split("\n")[-1])


def test_close_invokes_registered_teardown_callbacks(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="some/model")
    session = Session(config, provider=MagicMock())
    calls: list[str] = []

    session.register_teardown("Bash", lambda: calls.append("bash"))
    session.close()

    assert calls == ["bash"]


def test_close_is_idempotent(make_session_config: Callable[..., SessionConfig]) -> None:
    config = make_session_config(model="some/model")
    session = Session(config, provider=MagicMock())
    calls: list[str] = []

    session.register_teardown("Bash", lambda: calls.append("bash"))
    session.close()
    session.close()

    assert calls == ["bash"]


def test_reregistering_teardown_overwrites_not_accumulates(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="some/model")
    session = Session(config, provider=MagicMock())
    calls: list[str] = []

    session.register_teardown("Bash", lambda: calls.append("first"))
    session.register_teardown("Bash", lambda: calls.append("second"))
    session.close()

    assert calls == ["second"]


def test_deliver_notice_calls_the_registered_handler(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="some/model")
    session = Session(config, provider=MagicMock())
    notices: list[str] = []

    session.register_notice_handler(notices.append)
    session.deliver_notice("hook fired")

    assert notices == ["hook fired"]


def test_deliver_notice_is_a_noop_without_a_registered_handler(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="some/model")
    session = Session(config, provider=MagicMock())

    session.deliver_notice("nobody listening")


def test_reregistering_notice_handler_overwrites_not_accumulates(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    config = make_session_config(model="some/model")
    session = Session(config, provider=MagicMock())
    calls: list[str] = []

    session.register_notice_handler(lambda text: calls.append(f"first:{text}"))
    session.register_notice_handler(lambda text: calls.append(f"second:{text}"))
    session.deliver_notice("hi")

    assert calls == ["second:hi"]


def test_close_cascades_into_a_live_subagent_and_relays_its_note(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    parent = Session(make_session_config(), provider=MagicMock())
    child = Session(make_session_config(role_name="explorer"), provider=MagicMock(), parent=parent)
    handle = SubagentHandle(
        session=child, thread=threading.Thread(target=lambda: None), cancel_event=threading.Event(),
        role="explorer", title="task")
    parent.subagent_tracker.register(handle)
    parent.subagent_tracker.mark_finished(
        child.id, SubagentTurnOutcome(output="done", completed=True))

    parent.close()

    assert handle.delivered is True
    assert "done" in parent.messages[-1].content


def test_append_system_note_adds_a_complete_user_message(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock())

    session.append_system_note("some note")

    assert session.messages[-1].role == "user"
    assert session.messages[-1].content == "some note"
    assert session.messages[-1].processing_state == "complete"


def test_current_turn_handlers_is_none_outside_a_turn(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock())

    assert session.current_turn_handlers() is None


def test_file_accessed_is_a_noop_outside_a_turn(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock())

    session.file_accessed("/tmp/some/file.txt", "read")  # no exception is the assertion


def test_file_accessed_fires_the_current_turns_on_file_accessed_handler(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    session = Session(make_session_config(), provider=MagicMock())
    on_file_accessed = MagicMock()
    session._current_turn_handlers = TurnEventHandlers(on_file_accessed=on_file_accessed)

    session.file_accessed("/tmp/some/file.txt", "write")

    on_file_accessed.assert_called_once_with("/tmp/some/file.txt", "write")


def test_send_turn_with_resolve_mentions_false_leaves_at_mentions_literal(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    (tmp_path / "f.txt").write_text("secret contents")
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("ok")]
    config = make_session_config(model="some/model")
    session = Session(config, provider=mock_provider, process_config=ProcessConfig())

    session.send_turn("look at @f.txt", resolve_mentions=False)

    call_kwargs = mock_provider.send_prompt.call_args
    sent_messages = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs["messages"]
    assert not any("secret contents" in m.content for m in sent_messages)
    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[-1].content.endswith("look at @f.txt")


def test_permission_framework_deny_fails_closed_even_with_a_callback_given(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """`permission_framework="deny"` fails closed unconditionally -- it must not invoke
    `on_permission_ask` even if a caller supplied one."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    config.permission_framework = "deny"
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"Error: Permission requires confirmation: access {target}"
    on_permission_ask.assert_not_called()


def test_permission_framework_auto_approves_without_any_callback(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """`permission_framework="auto"` auto-approves via a synthesized "session"-scope grant,
    without ever invoking `on_permission_ask` (none is even given here)."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    config.permission_framework = "auto"
    process_config = ProcessConfig()
    session = _session_with_ask_tool(config, mock_provider, process_config)

    response = session.send_turn("try it")

    assert response == "done"
    assert _tool_response_content(session) == f"granted:{target}"
    assert config.read_dirs.allow == [tmp_path]
    # "session" scope must not have rippled into the process-config template or disk.
    assert process_config.session.read_dirs == DirRules()


def test_permission_framework_auto_ignores_an_on_permission_ask_callback(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """Even if a caller supplies `on_permission_ask`, `permission_framework="auto"` never
    invokes it -- the auto-approval is unconditional."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    config.permission_framework = "auto"
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="deny"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"granted:{target}"
    on_permission_ask.assert_not_called()


def test_permission_ask_once_retries_with_override_and_persists_nothing(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"granted:{target}"
    on_permission_ask.assert_called_once()
    (ask_ctx,), _ = on_permission_ask.call_args
    assert isinstance(ask_ctx.resource, PathResource)
    assert ask_ctx.resource.path == target
    assert ask_ctx.resource.is_write is True
    # "once" must not have touched the session's tables.
    assert config.read_dirs == DirRules()
    assert config.write_dirs == DirRules()


def test_permission_ask_once_retry_failure_falls_through_to_generic_error(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """If the retried call still fails even with the override applied, the result is an
    ordinary "Error: ..." tool_response -- never a second ask. Exercises
    Session._retry_after_permission_decision directly, via a mocked ToolRegistry, since
    provoking this from AskPermissionTool itself would require an artificial path mismatch."""
    target = tmp_path / "f.txt"
    config = make_session_config(model="some/model")

    mock_tool = MagicMock()
    mock_tool.apply.side_effect = ValueError("still broken")
    mock_registry = MagicMock()
    mock_registry.instantiate_tool.return_value = mock_tool

    session = Session(config, provider=MagicMock(), tool_registry=mock_registry)
    ask_exc = PermissionAskRequired(
        f"Permission requires confirmation: access {target}", path=target, is_write=True)
    call = ToolCallRequest(id="call_1", name="ask_permission", arguments="{}")

    outcome = session._retry_after_permission_decision(
        call, {}, ask_exc, PermissionDecision(action="allow"))

    assert outcome.result is None
    assert outcome.error == "still broken"
    assert outcome.category == "validation"
    mock_registry.instantiate_tool.assert_called_once_with(
        "ask_permission", permission_override=PermissionOverride(paths=frozenset({target})))


def test_permission_ask_session_scope_retries_after_applying_the_grant(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """`Session` applies the "session"-scope grant itself (via
    `klorb.permissions.grant.apply_permission_grant`) once `on_permission_ask` returns the
    decision -- `on_permission_ask` itself doesn't need to touch `config` at all."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    process_config = ProcessConfig()
    session = _session_with_ask_tool(config, mock_provider, process_config)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow", scope="session"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"granted:{target}"
    assert config.read_dirs.allow == [tmp_path]
    # "session" scope must not have rippled into the process-config template.
    assert process_config.session.read_dirs == DirRules()


def test_permission_ask_workspace_and_homedir_scope_persist_the_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    """`Session` applies and persists the "workspace"/"homedir" grant itself too, the same way
    as "session" scope -- this only pins that `Session` actually calls
    `apply_permission_grant()` and the retry succeeds; the grant's own computation and
    file-persistence semantics are covered directly in test_permission_grant.py."""
    monkeypatch.setattr(process_config_module, "get_klorb_config_dir", lambda: tmp_path / "homedir")
    target = tmp_path / "f.txt"
    scopes_and_files: list[tuple[Literal["workspace", "homedir"], Path]] = [
        ("workspace", tmp_path / ".klorb" / "klorb-config.json"),
        ("homedir", tmp_path / "homedir" / "klorb-config.json"),
    ]
    for scope, expected_file in scopes_and_files:
        mock_provider = MagicMock()
        mock_provider.send_prompt.side_effect = [
            _tool_call_reply([_ask_permission_call("call_1", target)]),
            _reply("done"),
        ]
        config = make_session_config(model="some/model")
        process_config = ProcessConfig()
        session = _session_with_ask_tool(config, mock_provider, process_config)
        on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow", scope=scope))

        response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

        assert response == "done"
        assert _tool_response_content(session) == f"granted:{target}"
        assert config.read_dirs.allow == [tmp_path]
        assert process_config.session.read_dirs.allow == [tmp_path]
        assert expected_file.is_file()


def test_permission_ask_workspace_scope_without_process_config_skips_the_ripple(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """A `Session` constructed with no `ProcessConfig` (`process_config=None`) has no
    process-wide template to ripple a "workspace"/"homedir" grant into --
    `apply_permission_grant` skips that step but still promotes the live `SessionConfig` and
    persists the grant to disk, since neither of those needs the `ProcessConfig` object
    itself (see test_permission_grant.py's own coverage of that behavior)."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)  # no process_config
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow", scope="workspace"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"granted:{target}"
    assert config.read_dirs.allow == [tmp_path]
    assert (tmp_path / ".klorb" / "klorb-config.json").is_file()


def test_permission_ask_deny_denies_without_any_retry(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="deny"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"Error: Permission denied: Permission requires " \
        f"confirmation: access {target}"
    # The provider was only asked for the initial tool-call round and the final plain reply --
    # never a third round retrying the tool call.
    assert mock_provider.send_prompt.call_count == 2


def test_permission_ask_other_includes_free_text_in_denial(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(
        return_value=PermissionDecision(action="deny", other_text="use /tmp instead"))

    session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    content = _tool_response_content(session)
    assert "use /tmp instead" in content
    assert "Permission denied" in content


def test_retry_last_turn_threads_on_permission_ask(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        RuntimeError("transient failure"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))
    callbacks = TurnEventHandlers(on_permission_ask=on_permission_ask)

    with pytest.raises(RuntimeError):
        session.send_turn("first attempt", callbacks)

    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_2", target)]),
        _reply("done"),
    ]
    response = session.retry_last_turn(callbacks)

    assert response == "done"
    assert on_permission_ask.call_count == 2


# --- MultiPermissionAskRequired: serial per-item asks (BashTool-shaped compound calls) ---


def _ask_multi_permission_call(id_: str, paths: list[Path]) -> tuple[str, str, str]:
    return id_, "ask_multi_permission", json.dumps({"paths": [str(p) for p in paths]})


def test_multi_ask_headless_fails_closed_like_a_generic_error(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)

    response = session.send_turn("try it")

    assert response == "done"
    assert "Permission requires confirmation" in _tool_response_content(session)


def test_multi_ask_asks_about_every_item_in_order_and_retries_once_all_approved(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == "granted:" + ",".join(str(p) for p in targets)
    # Every item was asked about individually, in order -- not collapsed into one prompt.
    assert on_permission_ask.call_count == 3
    asked_resources = [call.args[0].resource for call in on_permission_ask.call_args_list]
    assert all(isinstance(resource, PathResource) for resource in asked_resources)
    asked_paths = [resource.path for resource in asked_resources if isinstance(resource, PathResource)]
    assert asked_paths == targets


def test_multi_ask_stops_at_the_first_denial_and_never_asks_about_the_rest(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(side_effect=[
        PermissionDecision(action="allow"),
        PermissionDecision(action="deny"),
    ])

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert "Permission denied" in _tool_response_content(session)
    # Denied on the second item -- the third is never even asked about.
    assert on_permission_ask.call_count == 2
    assert config.write_dirs == DirRules()  # nothing was granted, not even the first, approved item


def test_multi_ask_permission_framework_auto_approves_every_item(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    config.permission_framework = "auto"
    session = _session_with_ask_tool(config, mock_provider)

    response = session.send_turn("try it")

    assert response == "done"
    assert _tool_response_content(session) == "granted:" + ",".join(str(p) for p in targets)


def test_multi_ask_permission_framework_deny_fails_closed_without_asking(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = make_session_config(model="some/model")
    config.permission_framework = "deny"
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert "Permission requires confirmation" in _tool_response_content(session)
    on_permission_ask.assert_not_called()


def test_multi_ask_resolve_threads_skill_field_into_ask_context(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A `MultiPermissionAskRequired` item carrying `.skill` (rather than `.path`/`.command`)
    must reach `on_permission_ask`'s `PermissionAskContext.skill` -- exercises
    `Session._resolve_multi_permission_ask` directly, since no real tool raises a skill-bearing
    `MultiPermissionAskRequired` today (only `BashTool` produces multi-item asks, and never for
    a skill)."""
    config = make_session_config(model="some/model")
    session = Session(config, provider=MagicMock(), tool_registry=MagicMock())
    item = PermissionAskItem("activate skill internal/s",
                             resource=SkillResource(skill_id=("internal", "s")))
    multi_ask_exc = MultiPermissionAskRequired("ask", items=[item])
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="deny"))
    call = ToolCallRequest(id="call_1", name="whatever", arguments="{}")

    session._resolve_multi_permission_ask(
        call, {}, multi_ask_exc, TurnEventHandlers(on_permission_ask=on_permission_ask))

    (ctx,), _ = on_permission_ask.call_args
    assert isinstance(ctx.resource, SkillResource)
    assert ctx.resource.skill_id == ("internal", "s")


def test_multi_ask_once_scope_builds_override_with_skill(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A `scope="once"` decision for a skill item must retry through a `PermissionOverride`
    whose `skills` set covers the pair -- exercises `Session._retry_after_multi_permission_
    decisions` directly, mirroring `test_permission_ask_once_retry_failure_falls_through_to_
    generic_error`'s mocked-registry approach for the single-ask path."""
    config = make_session_config(model="some/model")
    mock_tool = MagicMock()
    mock_tool.apply.return_value = "ok"
    mock_registry = MagicMock()
    mock_registry.instantiate_tool.return_value = mock_tool
    session = Session(config, provider=MagicMock(), tool_registry=mock_registry)
    item = PermissionAskItem("activate skill internal/s",
                             resource=SkillResource(skill_id=("internal", "s")))
    call = ToolCallRequest(id="call_1", name="whatever", arguments="{}")

    outcome = session._retry_after_multi_permission_decisions(
        call, {}, [item], [PermissionDecision(action="allow", scope="once")])

    assert outcome.result == "ok"
    assert outcome.error is None
    mock_registry.instantiate_tool.assert_called_once_with(
        "whatever", permission_override=PermissionOverride(skills=frozenset({("internal", "s")})))
    # "once" must not have touched the session's live skillRules.
    assert config.skill_rules.allow == []


def test_multi_ask_persistent_scope_applies_skill_grant(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A persistent-scope decision for a skill item must persist via
    `apply_skill_permission_grant` -- reflected in the live `SessionConfig.skill_rules` for a
    `"session"`-scope grant."""
    config = make_session_config(model="some/model")
    mock_tool = MagicMock()
    mock_tool.apply.return_value = "ok"
    mock_registry = MagicMock()
    mock_registry.instantiate_tool.return_value = mock_tool
    session = Session(config, provider=MagicMock(), tool_registry=mock_registry)
    item = PermissionAskItem("activate skill internal/s",
                             resource=SkillResource(skill_id=("internal", "s")))
    call = ToolCallRequest(id="call_1", name="whatever", arguments="{}")

    outcome = session._retry_after_multi_permission_decisions(
        call, {}, [item], [PermissionDecision(action="allow", scope="session")])

    assert outcome.result == "ok"
    assert outcome.error is None
    assert config.skill_rules.allow == [("internal", "s")]
    mock_registry.instantiate_tool.assert_called_once_with("whatever", permission_override=None)


# --- queued-message concurrency ---


def test_drain_queued_messages_never_drops_a_concurrent_enqueue(
    make_session_config: Callable[..., SessionConfig],
) -> None:
    """Drains racing a producer thread must hand every enqueued message to exactly one drain."""
    session = Session(make_session_config(), provider=MagicMock())
    total = 2000

    def producer() -> None:
        for index in range(total):
            session.enqueue_queued_message(QueuedMessage(message_text=str(index)))

    thread = threading.Thread(target=producer)
    thread.start()
    drained: list[QueuedMessage] = []
    while thread.is_alive():
        drained.extend(session.drain_queued_messages())
    thread.join(timeout=5.0)
    drained.extend(session.drain_queued_messages())

    assert sorted(int(m.message_text) for m in drained) == list(range(total))
