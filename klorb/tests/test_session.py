# © Copyright 2026 Aaron Kimball
"""Tests for klorb.session."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal
from unittest import mock
from unittest.mock import MagicMock

import fixtures.sample_models as sample_models_package
import fixtures.sample_tools as sample_tools_package
import pytest

from klorb import process_config as process_config_module
from klorb.api_provider import ProviderResponse, ResponseAborted
from klorb.message import Message, ToolCallRequest
from klorb.models.registry import ModelRegistry
from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired, PermissionOverride
from klorb.process_config import ProcessConfig
from klorb.role import CoordinatorRole
from klorb.session import (
    DEFAULT_MAX_TOOL_CALLS_PER_TURN,
    MAX_TOOL_CALL_ROUNDS,
    PERMISSION_FRAMEWORK_INTERJECTIONS,
    THINKING_EFFORT_TOKEN_BUDGETS,
    PermissionDecision,
    Session,
    SessionConfig,
    ThinkingEffort,
    ToolCallLimitExceeded,
    TurnEventHandlers,
    generate_session_id,
)
from klorb.system_prompts import DEFAULT_SYS_FILENAME, resolve_prompt_file
from klorb.tools.registry import ToolRegistry
from klorb.workspace import Workspace

SESSION_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-[a-z]+-[a-z]+$")

# What Session._resolve_system_prompt() produces for the default (coordinator) role, and for a
# role with no prompt files on an unregistered model. Computed via the same resolve_prompt_file()
# the session uses, so these stay correct even if a developer's own user-tier override files exist.
COORDINATOR_PROMPT = resolve_prompt_file("roles/coordinator/default.md")
DEFAULT_PROMPT = resolve_prompt_file(DEFAULT_SYS_FILENAME)

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
    return ToolRegistry(ProcessConfig(), config, package=sample_tools_package)


def test_session_config_defaults() -> None:
    config = SessionConfig()

    assert config.interactive is True
    assert config.thinking_enabled is True
    assert config.thinking_effort == "high"


def test_session_saves_config() -> None:
    config = SessionConfig(model="some/model", interactive=False)
    session = Session(config, provider=MagicMock())

    assert session.config is config


def test_session_role_defaults_to_coordinator() -> None:
    session = Session(SessionConfig(), provider=MagicMock())

    assert isinstance(session.role, CoordinatorRole)


def test_session_builds_role_from_config_role_name() -> None:
    session = Session(SessionConfig(role_name="auditor"), provider=MagicMock())

    assert session.role.name() == "auditor"


def test_generate_session_id_matches_expected_format() -> None:
    assert SESSION_ID_RE.match(generate_session_id())


def test_generate_session_id_is_unique_across_calls() -> None:
    assert generate_session_id() != generate_session_id()


def test_session_generates_id_when_not_given_explicitly() -> None:
    config = SessionConfig()
    session = Session(config, provider=MagicMock())

    assert SESSION_ID_RE.match(session.id)


def test_session_uses_explicitly_given_id() -> None:
    config = SessionConfig()
    session = Session(config, provider=MagicMock(), session_id="my-custom-id")

    assert session.id == "my-custom-id"


def test_session_scratchpad_path_delegates_to_scratchpad(tmp_path: Path) -> None:
    """Session itself does no scratchpad provisioning of its own -- it just owns a
    `klorb.tools.scratchpad.common.Scratchpad` and exposes its `.path`. Detailed provisioning
    behavior (fresh-vs-reused, tempdir independence) is covered by test_scratchpad_common.py."""
    shared = tmp_path / "team-scratchpad.md"
    shared.write_text("existing notes\n")

    session = Session(SessionConfig(), provider=MagicMock(), scratchpad_path=str(shared))

    assert session.scratchpad_path == shared


def test_session_scratchpad_grants_no_readdirs_writedirs_access() -> None:
    """A freshly created scratchpad's directory is not added to config.read_dirs/write_dirs --
    see docs/adrs/scratchpad-tools-bypass-permission-tables.md -- the dedicated Scratchpad
    tools reach it directly, with no permission-table check, instead."""
    config = SessionConfig()

    Session(config, provider=MagicMock())

    assert config.read_dirs == DirRules()
    assert config.write_dirs == DirRules()


def test_total_tokens_used_sums_recorded_message_tokens() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply(num_tokens=5, prompt_tokens=10)
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert session.total_tokens_used() == 10 + 5


def test_max_context_window_reads_registered_model_capabilities() -> None:
    config = SessionConfig(model="alpha")
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=MagicMock(), model_registry=registry)

    assert session.max_context_window() == 8_000


def test_max_context_window_none_when_model_unregistered() -> None:
    config = SessionConfig(model="some/unregistered-model")
    session = Session(config, provider=MagicMock())

    assert session.max_context_window() is None


def test_active_model_name_falls_back_to_config_model_when_unregistered() -> None:
    config = SessionConfig(model="some/unregistered-model")
    session = Session(config, provider=MagicMock())

    assert session.active_model_name() == "some/unregistered-model"


def test_active_model_name_invokes_registered_model_name() -> None:
    config = SessionConfig(model="alpha")
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=MagicMock(), model_registry=registry)

    assert session.active_model_name() == "alpha"


def test_send_turn_sends_prompt_to_active_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")

    response = session.send_turn("hi")

    assert response == "model reply"
    mock_provider.send_prompt.assert_called_once_with(
        session.messages[:-1], system_prompt=COORDINATOR_PROMPT, model="some/model",
        session_id="my-session-id", reasoning=None, tools=None, on_chunk=mock.ANY,
        on_thinking_chunk=mock.ANY, cancel_event=None)
    assert [m.content for m in session.messages] == [COORDINATOR_PROMPT, "hi", "model reply"]


def test_run_one_shot_delegates_to_send_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider, session_id="my-session-id")

    response = session.run_one_shot("hi")

    assert response == "model reply"
    mock_provider.send_prompt.assert_called_once_with(
        session.messages[:-1], system_prompt=COORDINATOR_PROMPT, model="some/model",
        session_id="my-session-id", reasoning=None, tools=None, on_chunk=mock.ANY,
        on_thinking_chunk=mock.ANY, cancel_event=None)


def test_send_turn_passes_system_prompt_from_registered_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="alpha", role_name=ROLE_WITHOUT_PROMPT_FILES)
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["system_prompt"] == "You are Alpha."


def test_role_prompt_outranks_registered_model_prompt() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="alpha")  # role_name defaults to "coordinator"
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["system_prompt"] == COORDINATOR_PROMPT


def test_unknown_role_on_unregistered_model_falls_back_to_default_prompt() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/unregistered-model", role_name=ROLE_WITHOUT_PROMPT_FILES)
    session = Session(config, provider=mock_provider)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["system_prompt"] == DEFAULT_PROMPT


def test_system_message_inserted_before_first_turn_for_registered_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="alpha", role_name=ROLE_WITHOUT_PROMPT_FILES)
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    assert [m.role for m in session.messages] == ["system", "user", "assistant"]
    assert session.messages[0].content == "You are Alpha."


def test_system_message_not_duplicated_across_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("r1"), _reply("r2")]
    config = SessionConfig(model="alpha")
    registry = ModelRegistry(package=sample_models_package)
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("first")
    session.send_turn("second")

    assert sum(1 for m in session.messages if m.role == "system") == 1


def test_system_message_holds_role_prompt_when_model_unregistered() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(SessionConfig(model="some/unregistered-model"), provider=mock_provider)

    session.send_turn("hi")

    assert session.messages[0].role == "system"
    assert session.messages[0].content == COORDINATOR_PROMPT


def test_system_message_inserted_ahead_of_tool_defs_message() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="alpha")
    registry = ModelRegistry(package=sample_models_package)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, model_registry=registry, tool_registry=tool_registry)

    session.send_turn("hi")

    assert [m.role for m in session.messages] == ["system", "tool_defs", "user", "assistant"]


def test_reasoning_defaults_to_high_effort_for_effort_style_thinking_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    session = Session(SessionConfig(model="beta"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"effort": "high"}


def test_reasoning_respects_configured_effort_level() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    config = SessionConfig(model="beta", thinking_effort="low")
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"effort": "low"}


def test_reasoning_uses_token_budget_for_tokens_style_thinking_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    session = Session(SessionConfig(model="gamma"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"max_tokens": THINKING_EFFORT_TOKEN_BUDGETS["high"]}


def test_reasoning_uses_custom_token_budgets_when_given() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    custom_budgets: dict[ThinkingEffort, int] = {"low": 1_000, "medium": 2_000, "high": 3_000}
    session = Session(
        SessionConfig(model="gamma"), provider=mock_provider, model_registry=registry,
        process_config=ProcessConfig(thinking_token_budgets=custom_budgets))

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] == {"max_tokens": 3_000}
    assert session.thinking_token_budgets == custom_budgets


def test_reasoning_none_when_thinking_disabled() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    config = SessionConfig(model="beta", thinking_enabled=False)
    session = Session(config, provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


def test_reasoning_none_when_model_does_not_support_thinking() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = ModelRegistry(package=sample_models_package)
    session = Session(SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


def test_reasoning_none_when_model_unregistered() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(SessionConfig(model="some/unregistered-model"), provider=mock_provider)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["reasoning"] is None


def test_send_turn_sends_full_history_to_provider() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("r1", num_tokens=5, prompt_tokens=10), _reply("r2")]
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("first")
    session.send_turn("second")

    second_call_messages = mock_provider.send_prompt.call_args_list[1].args[0]
    assert [m.content for m in second_call_messages] == [COORDINATOR_PROMPT, "first", "r1", "second"]


def test_token_delta_accounted_across_two_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _reply("r1", num_tokens=5, prompt_tokens=10),
        _reply("r2", num_tokens=8, prompt_tokens=23),
    ]
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("first")
    session.send_turn("second")

    system_message, user1, assistant1, user2, assistant2 = session.messages
    assert system_message.num_tokens == 0
    assert user1.num_tokens == 10
    assert assistant1.num_tokens == 5
    assert user2.num_tokens == 23 - (10 + 5)
    assert assistant2.num_tokens == 8


def test_send_turn_marks_user_message_error_and_reraises_on_provider_failure() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = RuntimeError("boom")
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    failed_message = session.messages[-1]
    assert failed_message.processing_state == "error"
    assert failed_message.last_error == "boom"


def test_retry_last_turn_mutates_same_message_on_success() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [RuntimeError("boom"), _reply("recovered")]
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    response = session.retry_last_turn()

    assert response == "recovered"
    assert len(session.messages) == 3
    user_message = session.messages[1]
    assert user_message.content == "hi"
    assert user_message.processing_state == "complete"
    assert user_message.last_error is None


def test_retry_last_turn_raises_when_nothing_errored() -> None:
    session = Session(SessionConfig(model="some/model"), provider=MagicMock())

    with pytest.raises(ValueError, match="No errored turn to retry."):
        session.retry_last_turn()


def test_streaming_chunks_populate_and_finalize_placeholder_message() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_chunk("Hel")
        on_chunk("lo")
        return _reply("Hello", num_tokens=2, prompt_tokens=10)

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert len(session.messages) == 3
    assistant_message = session.messages[-1]
    assert assistant_message.content == "Hello"
    assert assistant_message.streaming_content is None
    assert assistant_message.processing_state == "complete"
    assert assistant_message.num_tokens == 2


def test_send_turn_forwards_chunks_to_caller_on_chunk() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_chunk("Hel")
        on_chunk("lo")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)
    spy = MagicMock()

    session.send_turn("hi", TurnEventHandlers(on_chunk=spy))

    assert [call.args[0] for call in spy.call_args_list] == ["Hel", "lo"]


def test_streaming_thinking_chunks_populate_and_finalize_a_separate_placeholder_message() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_thinking_chunk("Let ")
        on_thinking_chunk("me think.")
        on_chunk("Hello")
        return _reply("Hello", num_tokens=2, prompt_tokens=10)

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert len(session.messages) == 4
    system_message, user_message, thinking_message, assistant_message = session.messages
    assert system_message.role == "system"
    assert user_message.role == "user"
    assert thinking_message.role == "thinking"
    assert thinking_message.content == "Let me think."
    assert thinking_message.streaming_content is None
    assert thinking_message.processing_state == "complete"
    assert thinking_message.num_tokens == 0
    assert assistant_message.role == "assistant"
    assert assistant_message.content == "Hello"


def test_send_turn_forwards_thinking_chunks_to_caller_on_thinking_chunk() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_thinking_chunk("Let ")
        on_thinking_chunk("me think.")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)
    spy = MagicMock()

    session.send_turn("hi", TurnEventHandlers(on_thinking_chunk=spy))

    assert [call.args[0] for call in spy.call_args_list] == ["Let ", "me think."]


def test_mid_stream_failure_marks_user_and_partial_assistant_message_error() -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_chunk("partial")
        raise RuntimeError("boom")

    mock_provider.send_prompt.side_effect = failing_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    _system_message, user_message, assistant_message = session.messages
    assert user_message.processing_state == "error"
    assert user_message.last_error == "boom"
    assert assistant_message.processing_state == "error"
    assert assistant_message.last_error == "boom"
    assert assistant_message.streaming_content == ["partial"]


def test_mid_stream_failure_marks_thinking_placeholder_error_too() -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_thinking_chunk("partial thought")
        raise RuntimeError("boom")

    mock_provider.send_prompt.side_effect = failing_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(RuntimeError):
        session.send_turn("hi")

    _system_message, user_message, thinking_message = session.messages
    assert user_message.processing_state == "error"
    assert thinking_message.processing_state == "error"
    assert thinking_message.last_error == "boom"
    assert thinking_message.streaming_content == ["partial thought"]


def test_abort_before_any_chunk_marks_user_message_aborted_not_removed() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = ResponseAborted()
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    with pytest.raises(ResponseAborted):
        session.send_turn("hi")

    _system_message, user_message = session.messages
    assert user_message.processing_state == "aborted"
    assert user_message.last_error is None
    assert user_message.num_tokens == 0


def test_abort_mid_stream_keeps_partial_assistant_and_thinking_content() -> None:
    mock_provider = MagicMock()

    def aborting_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_thinking_chunk("thinking out loud")
        on_chunk("partial rep")
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = aborting_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

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


def test_abort_after_a_completed_tool_call_round_keeps_that_rounds_messages() -> None:
    mock_provider = MagicMock()
    calls_made = 0

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        nonlocal calls_made
        calls_made += 1
        if calls_made == 1:
            return _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    config = SessionConfig(model="some/model")
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
    assert tool_response_message.content == "hi"
    user_message = session.messages[2]
    assert user_message.processing_state == "aborted"
    # The completed round's own prompt_tokens (10, per _tool_call_reply's default) counts
    # toward this turn even though the second round never finished.
    assert user_message.num_tokens == 10


def test_retry_last_turn_discards_partial_assistant_fragment_and_recovers() -> None:
    mock_provider = MagicMock()

    def failing_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None, on_chunk=None,
        on_thinking_chunk=None, cancel_event=None,
    ):
        on_chunk("partial")
        raise RuntimeError("boom")

    mock_provider.send_prompt.side_effect = failing_send_prompt
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

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


def test_no_tools_offered_when_tool_registry_unset() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["tools"] is None


def test_tool_definitions_offered_to_provider_when_tool_registry_set() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("hi")

    _, kwargs = mock_provider.send_prompt.call_args
    assert {d["function"]["name"] for d in kwargs["tools"]} == {
        "echo", "add", "ask_permission", "ask_multi_permission"}


def test_tool_defs_message_inserted_before_first_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("hi")

    assert session.messages[1].role == "tool_defs"
    assert [m.role for m in session.messages] == ["system", "tool_defs", "user", "assistant"]


def test_tool_defs_message_not_duplicated_across_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("r1"), _reply("r2")]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("first")
    session.send_turn("second")

    assert sum(1 for m in session.messages if m.role == "tool_defs") == 1


def test_no_tool_defs_message_when_tool_registry_unset() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    session = Session(SessionConfig(model="some/model"), provider=mock_provider)

    session.send_turn("hi")

    assert all(m.role != "tool_defs" for m in session.messages)


def test_tool_call_round_trip_dispatches_tool_and_returns_final_reply() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi there"}')]),
        _reply("final answer", num_tokens=4, prompt_tokens=20),
    ]
    config = SessionConfig(model="some/model")
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
    assert tool_response_message.content == "hi there"
    user_message = session.messages[2]
    assert user_message.processing_state == "complete"
    assert user_message.num_tokens == 20
    assert mock_provider.send_prompt.call_count == 2


def test_tool_call_round_trip_forwards_tool_calls_to_second_request() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    second_call_messages = mock_provider.send_prompt.call_args_list[1].args[0]
    assert [m.role for m in second_call_messages] == [
        "system", "tool_defs", "user", "tool_use", "tool_response"]


def test_unknown_tool_call_reports_error_to_model() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "NoSuchTool", "{}")]),
        _reply("recovered"),
    ]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response = session.send_turn("call a bogus tool")

    assert response == "recovered"
    tool_response_message = session.messages[4]
    assert tool_response_message.role == "tool_response"
    assert tool_response_message.content.startswith("Error:")


def test_round_limit_exceeded_raises_and_marks_user_message_error() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(
        model="some/model", max_tool_calls_per_turn=1_000, max_tool_calls_per_session=1_000)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ToolCallLimitExceeded):
        session.send_turn("loop forever")

    assert mock_provider.send_prompt.call_count == MAX_TOOL_CALL_ROUNDS + 1
    user_message = session.messages[2]
    assert user_message.processing_state == "error"
    assert str(MAX_TOOL_CALL_ROUNDS) in (user_message.last_error or "")


def test_per_turn_tool_call_limit_defaults_to_fifty() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ToolCallLimitExceeded, match="50 tool call"):
        session.send_turn("loop forever")

    tool_response_messages = [m for m in session.messages if m.role == "tool_response"]
    assert len(tool_response_messages) == DEFAULT_MAX_TOOL_CALLS_PER_TURN
    user_message = session.messages[2]
    assert user_message.processing_state == "error"


def test_per_turn_tool_call_limit_is_configurable() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=2)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ToolCallLimitExceeded, match="2 tool call"):
        session.send_turn("loop forever")

    tool_response_messages = [m for m in session.messages if m.role == "tool_response"]
    assert len(tool_response_messages) == 2


def test_per_turn_tool_call_limit_resets_between_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _reply("first done"),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
        _reply("second done"),
    ]
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=1)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response1 = session.send_turn("first")
    response2 = session.send_turn("second")

    assert response1 == "first done"
    assert response2 == "second done"


def test_per_session_tool_call_limit_accumulates_across_turns() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _reply("first done"),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
    ]
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=1_000, max_tool_calls_per_session=1)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    response1 = session.send_turn("first")
    assert response1 == "first done"

    with pytest.raises(ToolCallLimitExceeded, match="1 tool call"):
        session.send_turn("second")

    second_user_message = next(m for m in session.messages if m.role == "user" and m.content == "second")
    assert second_user_message.processing_state == "error"


def test_approving_turn_limit_increase_doubles_it_and_continues() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
        _tool_call_reply([("call_3", "echo", '{"message": "c"}')]),
        _reply("finally done"),
    ]
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=1)
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


def test_declining_turn_limit_increase_raises() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=1)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)
    on_limit_reached = MagicMock(return_value=False)

    with pytest.raises(ToolCallLimitExceeded, match="1 tool call"):
        session.send_turn("loop forever", TurnEventHandlers(on_tool_call_limit_reached=on_limit_reached))

    on_limit_reached.assert_called_once()
    assert config.max_tool_calls_per_turn == 1  # unchanged


def test_approving_session_limit_increase_doubles_it_and_continues() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _reply("first done"),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
        _reply("second done"),
    ]
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=1_000, max_tool_calls_per_session=1)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)
    on_limit_reached = MagicMock(return_value=True)

    callbacks = TurnEventHandlers(on_tool_call_limit_reached=on_limit_reached)
    response1 = session.send_turn("first", callbacks)
    response2 = session.send_turn("second", callbacks)

    assert response1 == "first done"
    assert response2 == "second done"
    on_limit_reached.assert_called_once()
    assert config.max_tool_calls_per_session == 2  # 1 -> 2


def test_no_callback_declines_without_asking() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
    config = SessionConfig(model="some/model", max_tool_calls_per_turn=1)
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    with pytest.raises(ToolCallLimitExceeded, match="1 tool call"):
        session.send_turn("loop forever")

    assert config.max_tool_calls_per_turn == 1  # unchanged


# --- on_tool_call ---


def test_on_tool_call_fires_once_per_successful_call() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi there"}')]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model")
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


def test_on_tool_call_fires_with_error_for_unknown_tool_name() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "NoSuchTool", "{}")]),
        _reply("recovered"),
    ]
    config = SessionConfig(model="some/model")
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    assert not (tmp_path / "tool-calls.log").exists()


def test_log_tool_calls_enabled_via_process_config_writes_request_and_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi there"}')]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model")
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "a"}')]),
        _reply("first done"),
        _tool_call_reply([("call_2", "echo", '{"message": "b"}')]),
        _reply("second done"),
    ]
    config = SessionConfig(model="some/model")
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_TOOL_CALLS", "true")
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    config = SessionConfig(model="some/model")
    tool_registry = _sample_tool_registry(config)
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)

    session.send_turn("please echo")

    assert (tmp_path / "tool-calls.log").exists()


def test_on_tool_call_fires_with_retried_result_after_permission_grant(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_ask_tool(config, mock_provider)
    on_tool_call = MagicMock()
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    session.send_turn("try it", TurnEventHandlers(
        on_tool_call=on_tool_call, on_permission_ask=on_permission_ask))

    on_tool_call.assert_called_once()
    (event,), _ = on_tool_call.call_args
    assert event.result == f"granted:{target}"
    assert event.error is None


def test_on_tool_call_fires_with_error_for_a_denied_permission_ask(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
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
    tool_registry = ToolRegistry(process_config or ProcessConfig(), config, package=sample_tools_package)
    return Session(
        config, provider=mock_provider, tool_registry=tool_registry, process_config=process_config)


def _tool_response_content(session: Session) -> str:
    tool_response = next(m for m in session.messages if m.role == "tool_response")
    assert isinstance(tool_response.content, str)
    return tool_response.content


def test_permission_ask_headless_fails_closed_like_a_generic_error(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_ask_tool(config, mock_provider)

    response = session.send_turn("try it")

    assert response == "done"
    assert _tool_response_content(session) == f"Error: Permission requires confirmation: access {target}"


def test_set_permission_framework_updates_config() -> None:
    config = SessionConfig(model="some/model", permission_framework="ask")
    session = Session(config, provider=MagicMock())

    session.set_permission_framework("auto")

    assert session.config.permission_framework == "auto"


def test_set_permission_framework_rejects_an_invalid_value() -> None:
    config = SessionConfig(model="some/model", permission_framework="ask")
    session = Session(config, provider=MagicMock())

    with pytest.raises(ValueError, match="not-a-real-mode"):
        session.set_permission_framework("not-a-real-mode")  # type: ignore[arg-type]

    assert session.config.permission_framework == "ask"
    assert session._pending_permission_framework_interjection is None


def test_set_permission_framework_queues_interjection_prepended_to_next_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
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
    assert user_messages[0].content == expected


def test_send_turn_with_no_pending_change_leaves_prompt_untouched() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider)

    session.send_turn("do the thing")

    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[0].content == "do the thing"


def test_multiple_permission_framework_changes_collapse_to_final_interjection() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
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
    assert user_messages[0].content == expected


def test_pending_interjection_applied_exactly_once() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("first"), _reply("second")]
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider)

    session.set_permission_framework("auto")
    session.send_turn("first turn")
    session.send_turn("second turn")

    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[1].content == "second turn"


def test_standing_interjection_appears_while_provider_returns_a_message() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("first"), _reply("second")]
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider)

    session.register_standing_interjection("SessionTerminal", lambda: "still open")
    session.send_turn("first turn")
    session.send_turn("second turn")

    expected = (
        '<SystemInterjection subject="SessionTerminal">\n'
        "still open\n"
        "</SystemInterjection>\n"
    )
    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[0].content == expected + "first turn"
    assert user_messages[1].content == expected + "second turn"


def test_standing_interjection_stops_once_provider_returns_none() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [_reply("first"), _reply("second")]
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider)

    live = {"alive": True}
    session.register_standing_interjection(
        "SessionTerminal", lambda: "still open" if live["alive"] else None)
    session.send_turn("first turn")
    live["alive"] = False
    session.send_turn("second turn")

    user_messages = [m for m in session.messages if m.role == "user"]
    assert "SessionTerminal" in user_messages[0].content
    assert user_messages[1].content == "second turn"


def test_reregistering_standing_interjection_overwrites_not_accumulates() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
    session = Session(config, provider=mock_provider)

    session.register_standing_interjection("SessionTerminal", lambda: "first version")
    session.register_standing_interjection("SessionTerminal", lambda: "second version")
    session.send_turn("do the thing")

    user_messages = [m for m in session.messages if m.role == "user"]
    assert user_messages[0].content.count("SystemInterjection") == 2  # one open + one close tag
    assert "second version" in user_messages[0].content
    assert "first version" not in user_messages[0].content


def test_standing_interjection_coexists_with_one_shot_permission_framework_interjection() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    config = SessionConfig(model="some/model")
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
    assert user_messages[0].content == expected


def test_close_invokes_registered_teardown_callbacks() -> None:
    config = SessionConfig(model="some/model")
    session = Session(config, provider=MagicMock())
    calls: list[str] = []

    session.register_teardown("Bash", lambda: calls.append("bash"))
    session.close()

    assert calls == ["bash"]


def test_close_is_idempotent() -> None:
    config = SessionConfig(model="some/model")
    session = Session(config, provider=MagicMock())
    calls: list[str] = []

    session.register_teardown("Bash", lambda: calls.append("bash"))
    session.close()
    session.close()

    assert calls == ["bash"]


def test_reregistering_teardown_overwrites_not_accumulates() -> None:
    config = SessionConfig(model="some/model")
    session = Session(config, provider=MagicMock())
    calls: list[str] = []

    session.register_teardown("Bash", lambda: calls.append("first"))
    session.register_teardown("Bash", lambda: calls.append("second"))
    session.close()

    assert calls == ["second"]


def test_permission_framework_deny_fails_closed_even_with_a_callback_given(tmp_path: Path) -> None:
    """`permission_framework="deny"` fails closed unconditionally -- it must not invoke
    `on_permission_ask` even if a caller supplied one."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path), permission_framework="deny")
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"Error: Permission requires confirmation: access {target}"
    on_permission_ask.assert_not_called()


def test_permission_framework_auto_approves_without_any_callback(tmp_path: Path) -> None:
    """`permission_framework="auto"` auto-approves via a synthesized "session"-scope grant,
    without ever invoking `on_permission_ask` (none is even given here)."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path), permission_framework="auto")
    process_config = ProcessConfig()
    session = _session_with_ask_tool(config, mock_provider, process_config)

    response = session.send_turn("try it")

    assert response == "done"
    assert _tool_response_content(session) == f"granted:{target}"
    assert config.read_dirs.allow == [tmp_path]
    # "session" scope must not have rippled into the process-config template or disk.
    assert process_config.session.read_dirs == DirRules()


def test_permission_framework_auto_ignores_an_on_permission_ask_callback(tmp_path: Path) -> None:
    """Even if a caller supplies `on_permission_ask`, `permission_framework="auto"` never
    invokes it -- the auto-approval is unconditional."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path), permission_framework="auto")
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="deny"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"granted:{target}"
    on_permission_ask.assert_not_called()


def test_permission_ask_once_retries_with_override_and_persists_nothing(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"granted:{target}"
    on_permission_ask.assert_called_once()
    (ask_ctx,), _ = on_permission_ask.call_args
    assert ask_ctx.path == target
    assert ask_ctx.is_write is True
    # "once" must not have touched the session's tables.
    assert config.read_dirs == DirRules()
    assert config.write_dirs == DirRules()


def test_permission_ask_once_retry_failure_falls_through_to_generic_error(tmp_path: Path) -> None:
    """If the retried call still fails even with the override applied, the result is an
    ordinary "Error: ..." tool_response -- never a second ask. Exercises
    Session._retry_after_permission_decision directly, via a mocked ToolRegistry, since
    provoking this from AskPermissionTool itself would require an artificial path mismatch."""
    target = tmp_path / "f.txt"
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))

    mock_tool = MagicMock()
    mock_tool.apply.side_effect = ValueError("still broken")
    mock_registry = MagicMock()
    mock_registry.instantiate_tool.return_value = mock_tool

    session = Session(config, provider=MagicMock(), tool_registry=mock_registry)
    ask_exc = PermissionAskRequired(
        f"Permission requires confirmation: access {target}", path=target, is_write=True)
    call = ToolCallRequest(id="call_1", name="ask_permission", arguments="{}")

    result, error = session._retry_after_permission_decision(
        call, {}, ask_exc, PermissionDecision(action="allow"))

    assert result is None
    assert error == "still broken"
    mock_registry.instantiate_tool.assert_called_once_with(
        "ask_permission", permission_override=PermissionOverride(paths=frozenset({target})))


def test_permission_ask_session_scope_retries_after_applying_the_grant(tmp_path: Path) -> None:
    """`Session` applies the "session"-scope grant itself (via
    `klorb.permissions.grant.apply_permission_grant`) once `on_permission_ask` returns the
    decision -- `on_permission_ask` itself doesn't need to touch `config` at all."""
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`Session` applies and persists the "workspace"/"homedir" grant itself too, the same way
    as "session" scope -- this only pins that `Session` actually calls
    `apply_permission_grant()` and the retry succeeds; the grant's own computation and
    file-persistence semantics are covered directly in test_permission_grant.py."""
    monkeypatch.setattr(process_config_module, "KLORB_CONFIG_DIR", tmp_path / "homedir")
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
        config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
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
    tmp_path: Path,
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
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_ask_tool(config, mock_provider)  # no process_config
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow", scope="workspace"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"granted:{target}"
    assert config.read_dirs.allow == [tmp_path]
    assert (tmp_path / ".klorb" / "klorb-config.json").is_file()


def test_permission_ask_deny_denies_without_any_retry(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="deny"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == f"Error: Permission denied: Permission requires " \
        f"confirmation: access {target}"
    # The provider was only asked for the initial tool-call round and the final plain reply --
    # never a third round retrying the tool call.
    assert mock_provider.send_prompt.call_count == 2


def test_permission_ask_other_includes_free_text_in_denial(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(
        return_value=PermissionDecision(action="deny", other_text="use /tmp instead"))

    session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    content = _tool_response_content(session)
    assert "use /tmp instead" in content
    assert "Permission denied" in content


def test_retry_last_turn_threads_on_permission_ask(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_permission_call("call_1", target)]),
        RuntimeError("transient failure"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
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


def test_multi_ask_headless_fails_closed_like_a_generic_error(tmp_path: Path) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_ask_tool(config, mock_provider)

    response = session.send_turn("try it")

    assert response == "done"
    assert "Permission requires confirmation" in _tool_response_content(session)


def test_multi_ask_asks_about_every_item_in_order_and_retries_once_all_approved(
    tmp_path: Path,
) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert _tool_response_content(session) == "granted:" + ",".join(str(p) for p in targets)
    # Every item was asked about individually, in order -- not collapsed into one prompt.
    assert on_permission_ask.call_count == 3
    asked_paths = [call.args[0].path for call in on_permission_ask.call_args_list]
    assert asked_paths == targets


def test_multi_ask_stops_at_the_first_denial_and_never_asks_about_the_rest(tmp_path: Path) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = SessionConfig(model="some/model", workspace=Workspace(path=tmp_path))
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


def test_multi_ask_permission_framework_auto_approves_every_item(tmp_path: Path) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path), permission_framework="auto")
    session = _session_with_ask_tool(config, mock_provider)

    response = session.send_turn("try it")

    assert response == "done"
    assert _tool_response_content(session) == "granted:" + ",".join(str(p) for p in targets)


def test_multi_ask_permission_framework_deny_fails_closed_without_asking(tmp_path: Path) -> None:
    targets = [tmp_path / "a.txt", tmp_path / "b.txt"]
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([_ask_multi_permission_call("call_1", targets)]),
        _reply("done"),
    ]
    config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path), permission_framework="deny")
    session = _session_with_ask_tool(config, mock_provider)
    on_permission_ask = MagicMock(return_value=PermissionDecision(action="allow"))

    response = session.send_turn("try it", TurnEventHandlers(on_permission_ask=on_permission_ask))

    assert response == "done"
    assert "Permission requires confirmation" in _tool_response_content(session)
    on_permission_ask.assert_not_called()
