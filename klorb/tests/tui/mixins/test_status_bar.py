# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.status_bar.StatusBarMixin."""

import threading
from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

from fixtures.sample_models import sample_model_registry
from textual.widgets import Static
from tui.conftest import TEST_SESSION_ID, _reply, _session, _wait_until

from klorb.api_provider import ProviderResponse
from klorb.message import Message
from klorb.session import Session, SessionConfig
from klorb.token_estimate import estimate_tokens
from klorb.tui.app import ReplApp
from klorb.tui.constants import (
    OUTPUT_TOKENS_ID,
    PALETTE_HINT_ID,
    PERMISSION_BADGE_ID,
    PROMPT_INPUT_ID,
    STATUS_BAR_ID,
)
from klorb.tui.formatting import format_token_count
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.status_widgets import PALETTE_HINT_TEXT, PermissionBadge


async def test_status_bar_shows_zero_tokens_against_model_context_window_on_mount() -> None:
    mock_provider = MagicMock()
    registry = sample_model_registry()
    session = Session(
        SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry,
        session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test():
        status_bar = app.query_one(f"#{STATUS_BAR_ID}", Static)
        assert status_bar.content == "\u2191 0 / 8k"


async def test_status_bar_updates_after_a_turn_completes() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = sample_model_registry()
    session = Session(
        SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry,
        session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        status_bar = app.query_one(f"#{STATUS_BAR_ID}", Static)
        assert status_bar.content == f"\u2191 {format_token_count(session.total_tokens_used())} / 8k"


async def test_status_bar_updates_mid_stream_before_the_turn_completes() -> None:
    """The footer's token tally must track each incoming chunk, not just settle once the
    whole turn is done.
    """
    mock_provider = MagicMock()
    first_chunk_rendered = threading.Event()
    release_second_chunk = threading.Event()

    def fake_send_prompt(
        messages: Any, system_prompt: Any = None, model: Any = None, session_id: Any = None,
        reasoning: Any = None, tools: Any = None, drop_reasoning: Any = False, on_chunk: Any = None,
        on_thinking_chunk: Any = None, on_reasoning_details: Any = None,
        cache_mgmt_style: Any = None, cancel_event: Any = None,
    ) -> Any:
        on_chunk("Hello")
        first_chunk_rendered.set()
        assert release_second_chunk.wait(timeout=5)
        on_chunk(" world")
        return ProviderResponse(
            message=Message(
                content="Hello world", role="assistant",
                num_tokens=estimate_tokens("Hello world"), processing_state="complete",
                timestamp=datetime.now()),
            prompt_tokens=1)

    mock_provider.send_prompt.side_effect = fake_send_prompt
    registry = sample_model_registry()
    session = Session(
        SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry,
        session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")

        await _wait_until(pilot, first_chunk_rendered.is_set)

        status_bar = app.query_one(f"#{STATUS_BAR_ID}", Static)
        mid_stream_tally = status_bar.content
        mid_stream_total = session.total_tokens_used()
        assert mid_stream_tally != "\u2191 0 / 8k"
        assert mid_stream_tally == f"\u2191 {format_token_count(mid_stream_total)} / 8k"

        release_second_chunk.set()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert status_bar.content == f"\u2191 {format_token_count(session.total_tokens_used())} / 8k"
        # The system/tool_defs/user content already counted toward the mid-stream tally, so a
        # one-word difference in the final reply's length may not shift the SI-rounded display
        # string -- assert the underlying exact total grew instead of the rendered text.
        assert session.total_tokens_used() > mid_stream_total


async def test_status_bar_omits_limit_when_model_unregistered() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        status_bar = app.query_one(f"#{STATUS_BAR_ID}", Static)
        assert status_bar.content == "\u2191 0"


async def test_output_tokens_widget_shows_zero_on_mount() -> None:
    mock_provider = MagicMock()
    registry = sample_model_registry()
    session = Session(
        SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry,
        session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test():
        output_tokens = app.query_one(f"#{OUTPUT_TOKENS_ID}", Static)
        assert output_tokens.content == "\u2193 0"


async def test_output_tokens_widget_updates_after_a_turn_completes() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    registry = sample_model_registry()
    session = Session(
        SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry,
        session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        output_tokens = app.query_one(f"#{OUTPUT_TOKENS_ID}", Static)
        assert output_tokens.content == f"\u2193 {format_token_count(session.total_output_tokens_used())}"


async def test_output_tokens_widget_updates_mid_stream_before_the_turn_completes() -> None:
    mock_provider = MagicMock()
    first_chunk_rendered = threading.Event()
    release_second_chunk = threading.Event()

    def fake_send_prompt(
        messages: Any, system_prompt: Any = None, model: Any = None, session_id: Any = None,
        reasoning: Any = None, tools: Any = None, drop_reasoning: Any = False, on_chunk: Any = None,
        on_thinking_chunk: Any = None, on_reasoning_details: Any = None,
        cache_mgmt_style: Any = None, cancel_event: Any = None,
    ) -> Any:
        on_chunk("Hello")
        first_chunk_rendered.set()
        assert release_second_chunk.wait(timeout=5)
        on_chunk(" world")
        return _reply("Hello world")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    registry = sample_model_registry()
    session = Session(
        SessionConfig(model="alpha"), provider=mock_provider, model_registry=registry,
        session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")

        await _wait_until(pilot, first_chunk_rendered.is_set)

        output_tokens = app.query_one(f"#{OUTPUT_TOKENS_ID}", Static)
        assert output_tokens.content == f"\u2193 {format_token_count(session.total_output_tokens_used())}"
        assert output_tokens.content != "\u2193 0"

        release_second_chunk.set()
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert output_tokens.content == f"\u2193 {format_token_count(session.total_output_tokens_used())}"


async def test_permission_badge_shows_the_session_permission_framework() -> None:
    mock_provider = MagicMock()
    session = Session(
        SessionConfig(model="some/model", permission_framework="auto"),
        provider=mock_provider, session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test():
        badge = app.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        assert str(badge.render()) == "[auto]"


async def test_permission_badge_defaults_to_ask() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        badge = app.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        assert str(badge.render()) == "[ask]"


async def test_shift_tab_cycles_permission_framework() -> None:
    """Wraps every read of `permission_framework` in `str(...)` -- comparing the same
    `Literal["ask", "auto", "deny"]`-typed attribute against several different literals in
    sequence, with no reassignment mypy can see in between (it happens inside `ReplApp`'s
    key handling), would otherwise have mypy narrow the attribute to the first-asserted
    literal and flag every later assertion as unreachable."""
    mock_provider = MagicMock()
    session = _session(mock_provider)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        assert str(session.config.permission_framework) == "ask"

        await pilot.press("shift+tab")
        await pilot.pause()
        assert str(session.config.permission_framework) == "auto"
        assert session._pending_permission_framework_interjection is not None
        badge = app.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        assert str(badge.render()) == "[auto]"

        await pilot.press("shift+tab")
        await pilot.pause()
        assert str(session.config.permission_framework) == "deny"

        await pilot.press("shift+tab")
        await pilot.pause()
        assert str(session.config.permission_framework) == "ask"


async def test_clicking_permission_badge_cycles_permission_framework() -> None:
    """A mouse click on the badge advances the framework the same way Shift+Tab does (see
    `PermissionBadge.Clicked` / `on_permission_badge_clicked`)."""
    mock_provider = MagicMock()
    session = _session(mock_provider)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        assert str(session.config.permission_framework) == "ask"

        await pilot.click(PermissionBadge)
        await pilot.pause()
        assert str(session.config.permission_framework) == "auto"
        badge = app.query_one(f"#{PERMISSION_BADGE_ID}", PermissionBadge)
        assert str(badge.render()) == "[auto]"

        await pilot.click(PermissionBadge)
        await pilot.pause()
        assert str(session.config.permission_framework) == "deny"


async def test_palette_hint_shown_only_while_the_box_is_empty_or_bare_gt() -> None:
    app = ReplApp(session=_session(MagicMock()))

    async with app.run_test() as pilot:
        hint = app.query_one(f"#{PALETTE_HINT_ID}", Static)
        assert str(hint.render()).strip() == PALETTE_HINT_TEXT

        await pilot.press(">")
        await pilot.pause()
        assert str(hint.render()).strip() == PALETTE_HINT_TEXT

        await pilot.press("x")
        await pilot.pause()
        assert str(hint.render()) == ""
