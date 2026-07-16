# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.prompt_submission.PromptSubmissionMixin."""

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from textual.containers import VerticalScroll
from textual.widgets import Markdown, Static
from tui.conftest import (
    _invoke_clear_session,
    _reply,
    _session,
    _session_with_tools,
    _tool_call_reply,
    _wait_until,
)

from klorb.api_provider import ResponseAborted
from klorb.logging_config import session_log_path
from klorb.process_config import ProcessConfig
from klorb.session import DEFAULT_MAX_TOOL_CALLS_PER_TURN, SessionConfig
from klorb.tui.app import ReplApp
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.tool_call_widgets import ToolCallStatic


async def test_submitting_an_empty_prompt_does_nothing() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "   "
        await pilot.press("enter")
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(history.query(Static).exclude(".mascot")) == 0

    mock_provider.send_prompt.assert_not_called()


async def test_second_submit_while_a_turn_is_in_flight_is_dropped() -> None:
    """A submit that arrives while a turn is still running is ignored (`_turn_in_flight`), so a
    double Enter / a bracketed paste with a trailing newline can't launch a second, concurrent
    `_send_prompt` worker. The prompt input's `disabled` flag can't be relied on for this: Enter
    posts its `Submitted` synchronously but `disabled` is set only when the app handles it, so two
    submits queued inside that window both reach the handler before either disables the box."""
    mock_provider = MagicMock()
    first_turn_streaming = threading.Event()
    release_first_turn = threading.Event()

    def fake_send_prompt(*args: Any, **kwargs: Any) -> Any:
        first_turn_streaming.set()
        release_first_turn.wait(timeout=5)
        return _reply()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "first"
        await pilot.press("enter")

        # First turn is now blocked inside the worker; try to launch a second one directly (the
        # `disabled` box would swallow a real keypress, so drive the submit handler ourselves to
        # reproduce the queued-Submitted race that `disabled` can't guard).
        await _wait_until(pilot, first_turn_streaming.is_set)
        app._submit_prompt("second")
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        # Only the first prompt was ever echoed; the second submit was dropped.
        prompt_widgets = history.query(".prompt")
        assert len(prompt_widgets) == 1
        assert prompt_widgets.first(Static).content == "first"

        release_first_turn.set()
        await app.workers.wait_for_complete()
        await pilot.pause()

        # With the first turn finished the guard is cleared, so a fresh submit goes through.
        assert app._turn_in_flight is False

    assert mock_provider.send_prompt.call_count == 1


async def test_provider_error_is_shown_in_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = RuntimeError("boom")
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        error_widget = history.query_one(".error", Static)

        assert "boom" in str(error_widget.content)
        assert prompt_input.disabled is False


async def test_aborted_response_keeps_prompt_in_history_and_clears_input() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = ResponseAborted()
    session = _session(mock_provider)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "what is 2+2?"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.query_one(".prompt", Static)
        history.query_one(".interrupted", Static)
        assert prompt_input.text == ""
        assert prompt_input.disabled is False

    # The user turn stays in history, tagged "aborted" rather than discarded — nothing ever
    # streamed in before the abort, so there's no assistant/thinking placeholder alongside it.
    assert [m.role for m in session.messages] == ["system", "user"]
    assert session.messages[-1].processing_state == "aborted"


async def test_escape_aborts_a_streaming_response() -> None:
    mock_provider = MagicMock()
    streaming_started = threading.Event()

    def fake_send_prompt(*args: Any, cancel_event: threading.Event | None = None, **kwargs: Any) -> Any:
        assert cancel_event is not None
        streaming_started.set()
        cancel_event.wait(timeout=5)
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = _session(mock_provider)
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "what is 2+2?"
        await pilot.press("enter")

        await _wait_until(pilot, streaming_started.is_set)
        assert app.check_action("abort_response", ()) is True

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.query_one(".prompt", Static)
        history.query_one(".interrupted", Static)
        assert prompt_input.text == ""
        assert prompt_input.disabled is False
        assert app.check_action("abort_response", ()) is False

    # The user turn stays in history, tagged "aborted", rather than being discarded.
    assert [m.role for m in session.messages] == ["system", "user"]
    assert session.messages[-1].processing_state == "aborted"


async def test_turn_in_flight_cleared_when_worker_raises_base_exception() -> None:
    """A `BaseException` out of `send_turn` (e.g. worker cancellation) slips past `except
    Exception`; the `finally` backstop (`_ensure_turn_finished`) must still clear
    `_turn_in_flight` and re-enable input so the app doesn't wedge — see
    docs/specs/interrupt-and-liveness-watchdog.md."""

    class _Boom(BaseException):
        pass

    streaming_started = threading.Event()
    app = ReplApp(session=_session(MagicMock()))

    def boom(*args: Any, **kwargs: Any) -> Any:
        streaming_started.set()
        raise _Boom()

    app._session.send_turn = boom  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).text = "hi"
        await pilot.press("enter")
        await _wait_until(pilot, streaming_started.is_set)
        await _wait_until(pilot, lambda: app._turn_in_flight is False)

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        assert prompt_input.disabled is False


async def test_aborting_a_turn_keeps_its_completed_tool_call_widgets() -> None:
    mock_provider = MagicMock()
    streaming_started = threading.Event()
    calls_made = 0

    def fake_send_prompt(*args: Any, cancel_event: threading.Event | None = None, **kwargs: Any) -> Any:
        nonlocal calls_made
        calls_made += 1
        if calls_made == 1:
            return _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
        assert cancel_event is not None
        streaming_started.set()
        cancel_event.wait(timeout=5)
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")

        await _wait_until(pilot, streaming_started.is_set)

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(list(history.query(ToolCallStatic))) == 1

        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        # The echo call already ran, with a real (if trivial) side effect, before the second
        # round's abort — it stays in the transcript exactly like a completed turn's would.
        assert len(list(history.query(ToolCallStatic))) == 1
        history.query_one(".interrupted", Static)

    assert [m.role for m in session.messages] == [
        "system", "tool_defs", "user", "tool_use", "tool_response"]
    assert session.messages[2].processing_state == "aborted"
    assert len(app._tool_call_widgets) == 1


async def test_each_tool_call_round_gets_its_own_thinking_and_response_blocks() -> None:
    mock_provider = MagicMock()
    calls_made = 0

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ):
        nonlocal calls_made
        calls_made += 1
        assert on_thinking_chunk is not None
        assert on_chunk is not None
        if calls_made == 1:
            on_thinking_chunk("Round one thinking.")
            on_chunk("Round one reply.")
            return _tool_call_reply([("call_1", "echo", '{"message": "hi"}')])
        on_thinking_chunk("Round two thinking.")
        on_chunk("Round two reply.")
        return _reply("Round two reply.")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        thinking_bodies = list(history.query(".thinking-body").results(Static))
        response_widgets = list(history.query(Markdown))

        # Each round gets its own thinking/response widgets, rather than one growing block
        # absorbing both rounds' text across the tool call in between.
        assert [w.content for w in thinking_bodies] == [
            "Round one thinking.", "Round two thinking."]
        assert [w.source for w in response_widgets] == ["Round one reply.", "Round two reply."]

        # The tool call sits between the two rounds' blocks in the transcript, matching the
        # actual time-order: round one drafts, then the tool call runs, then round two drafts.
        children = list(history.children)
        tool_call_index = children.index(history.query_one(ToolCallStatic))
        assert children.index(response_widgets[0]) < tool_call_index < children.index(response_widgets[1])


# --- shell commands ("!"-prefixed) ---


async def test_shell_command_echoes_and_shows_output() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!echo hello"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        widgets = list(history.query(Static).exclude(".mascot"))
        assert widgets[0].content == "!echo hello"
        assert widgets[1].content == "hello\n"
        assert prompt_input.disabled is False
        assert prompt_input.text == ""

    mock_provider.send_prompt.assert_not_called()


async def test_shell_command_preserves_newlines_across_multiple_lines() -> None:
    """Regression test: shell output used to be rendered via a `Markdown` widget, whose
    CommonMark rendering collapses a single newline inside a paragraph into a soft line
    break (a space). Shell output isn't markdown, so its newlines must survive verbatim.
    """
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!printf 'a\\nb\\nc\\n'"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        output_widget = list(history.query(Static).exclude(".mascot"))[1]
        assert output_widget.content == "a\nb\nc\n"


async def test_shell_command_exit_code_shown_as_error() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!exit 7"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        error_widget = history.query_one(".error", Static)
        assert "status 7" in str(error_widget.content)
        assert prompt_input.disabled is False


async def test_shell_command_uses_configured_shell_binary() -> None:
    mock_provider = MagicMock()
    process_config = ProcessConfig(shell_command="/no/such/shell")
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!echo hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        output_widget = list(history.query(Static).exclude(".mascot"))[1]
        assert "/no/such/shell not found" in str(output_widget.content)


async def test_shell_command_timeout_kills_it_and_shows_an_error() -> None:
    mock_provider = MagicMock()
    process_config = ProcessConfig(shell_timeout_seconds=0.2)
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!sleep 5"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        error_widget = history.query_one(".error", Static)
        assert "timed out" in str(error_widget.content)
        assert prompt_input.disabled is False


async def test_clear_gives_the_new_session_a_fresh_tool_registry() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        await _invoke_clear_session(pilot)

        assert app._session.tool_registry is not None
        assert "ReadFile" in {tool.name() for tool in app._session.tool_registry.tools()}


async def test_clear_replaces_session_and_resets_history() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    process_config = ProcessConfig(
        session_cli_flags={"model": "some/model", "interactive": True})
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        original_session_id = app._session.id

        await _invoke_clear_session(pilot)

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(history.children) == 1  # Just the "Session cleared." notice.
        assert app._session.id != original_session_id
        assert app._session.config.model == "some/model"
        assert app._session.messages == []


async def test_clear_closes_the_outgoing_session() -> None:
    """`clear_session()` must tear down the outgoing `Session` (e.g. killing any live
    `BashTool` persistent shell it holds) before replacing it -- otherwise a registered
    teardown callback would never run and the resource it guards would leak."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        outgoing_session = app._session
        calls: list[str] = []
        outgoing_session.register_teardown("Bash", lambda: calls.append("closed"))

        await _invoke_clear_session(pilot)

        assert calls == ["closed"]
        assert app._session is not outgoing_session


async def test_clear_carries_over_thinking_settings_from_process_config(
    tmp_path: Path,
) -> None:
    """`/clear` rebuilds the new session's `SessionConfig` by re-reading the config layers
    from disk and applying CLI flags on top (see `ReplApp.clear_session`), so a setting
    changed via the palette survives a `/clear` only if it's persisted to a config file
    `load_process_config()` reads. `set_thinking_enabled`/`set_thinking_effort` persist to
    `user_config_path()` (see `persist_session_default`), so pointing the REPL's
    `user_config_path` at the same file the conftest-isolated `load_process_config()` reads
    makes the carry-over work through that disk reload rather than the old in-memory
    `ProcessConfig.session` template copy.
    """
    from klorb.process_config import user_config_path as process_user_config_path

    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    with patch("klorb.tui.app.user_config_path", return_value=process_user_config_path()):
        async with app.run_test() as pilot:
            app.set_thinking_enabled(False)
            app.set_thinking_effort("low")

            await _invoke_clear_session(pilot)

            assert app._session.config.thinking_enabled is False


async def test_clear_reloads_session_config_from_disk(
    tmp_path: Path,
) -> None:
    """`/clear` re-reads the config layers from disk into the new session's
    `SessionConfig`, so a config-file edit made between startup and the `/clear` takes
    effect rather than being silently ignored (the old behavior, which copied the
    in-memory `ProcessConfig.session` template that was loaded once at startup).

    Writes a `sessionDefaults.tools.maxCallsPerTurn` to the conftest-isolated per-user config file
    (`process_config.user_config_path()`, the one `load_process_config()` reads) after
    the app is already running, then asserts the cleared session picked it up.
    """
    from klorb.process_config import user_config_path as process_user_config_path
    from klorb.schema_envelope import write_versioned_json

    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        # Sanity check: the session starts with the tool-call limit the session was constructed with,
        # not the one we're about to write to disk.
        assert app._session.config.max_tool_calls_per_turn == DEFAULT_MAX_TOOL_CALLS_PER_TURN

        raised_limit = 6 * DEFAULT_MAX_TOOL_CALLS_PER_TURN

        config_path = process_user_config_path()
        write_versioned_json(
            config_path,
            {"sessionDefaults": {"tools.maxCallsPerTurn": raised_limit}},
            schema_name="klorb-config", schema_version="1.0.0")

        await _invoke_clear_session(pilot)

        assert app._session.config.max_tool_calls_per_turn == raised_limit


async def test_clear_applies_cli_flags_on_top_of_disk(
    tmp_path: Path,
) -> None:
    """CLI flags are re-applied on top of the disk-reloaded `SessionConfig` during
    `/clear`, so a `--model` (etc.) passed to this invocation survives a `/clear` and wins
    over any disk config value — the same precedence it had at startup.
    """
    from klorb.process_config import user_config_path as process_user_config_path
    from klorb.schema_envelope import write_versioned_json


    raised_limit = 7 * DEFAULT_MAX_TOOL_CALLS_PER_TURN
    mock_provider = MagicMock()
    process_config = ProcessConfig(
        session_cli_flags={"max_tool_calls_per_turn": raised_limit, "interactive": True})
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test() as pilot:
        config_path = process_user_config_path()
        write_versioned_json(
            config_path,
            {"sessionDefaults": {"model": "from/disk"}},
            schema_name="klorb-config", schema_version="1.0.0")

        await _invoke_clear_session(pilot)

        # The CLI flag wins over the disk value, the same way it did at startup.
        assert app._session.config.max_tool_calls_per_turn == raised_limit
        # And the process-level template carries it too, so a *second* /clear would also
        # keep it.
        assert app._process_config.session.max_tool_calls_per_turn == raised_limit
        assert app._process_config.session_cli_flags == \
            {"max_tool_calls_per_turn": raised_limit, "interactive": True}


async def test_clear_preserves_argv_and_cli_flags_across_reload() -> None:
    """`clear_session()` reloads process-only fields from disk via `load_process_config()`,
    which never populates `argv`/`cli_flags` (those are set once by `klorb.cli.main()`).
    The reload must not wipe them back to their empty defaults — otherwise a second
    `/clear` would lose the CLI overrides the first one preserved.
    """
    mock_provider = MagicMock()
    argv = ["klorb", "--model", "from/cli"]
    cli_flags = {"model": "from/cli", "interactive": True}
    process_config = ProcessConfig(argv=argv, session_cli_flags=cli_flags)
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test() as pilot:
        await _invoke_clear_session(pilot)

        assert app._process_config.argv == argv
        assert app._process_config.session_cli_flags == cli_flags


async def test_clear_does_not_disable_input_or_send_to_provider() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        await _invoke_clear_session(pilot)

        assert prompt_input.disabled is False

    mock_provider.send_prompt.assert_not_called()


async def test_clear_rotates_log_file_when_session_log_enabled() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider), session_log_enabled=True)

    with patch("klorb.tui.mixins.prompt_submission.configure_logging") as mock_configure_logging:
        async with app.run_test() as pilot:
            await _invoke_clear_session(pilot)

    mock_configure_logging.assert_called_once_with(
        repl_mode=True, log_path=session_log_path(app._session.id))


async def test_clear_skips_log_rotation_when_session_log_disabled() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider), session_log_enabled=False)

    with patch("klorb.tui.mixins.prompt_submission.configure_logging") as mock_configure_logging:
        async with app.run_test() as pilot:
            await _invoke_clear_session(pilot)

    mock_configure_logging.assert_not_called()


# --- input history (up/down-arrow recall) ---


async def test_streaming_response_updates_widget_progressively() -> None:
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ):
        assert on_chunk is not None
        on_chunk("Hel")
        on_chunk("lo")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        response_widgets = list(history.query(Markdown))

        assert len(response_widgets) == 1
        assert response_widgets[0].source == "Hello"


async def test_streaming_updates_stay_pinned_to_the_bottom_when_the_user_is_at_the_bottom() -> None:
    """`ReplApp._scroll_if_pinned` is where the app decides whether to follow new streaming
    content to the bottom; spying on it (rather than asserting on `history.scroll_y` /
    `max_scroll_y` directly) tests that decision without depending on exactly when Textual's
    own deferred `scroll_end()` settles the viewport, which is unrelated to the bug this
    covers and isn't something this test needs to pin down.
    """
    mock_provider = MagicMock()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ):
        assert on_thinking_chunk is not None
        assert on_chunk is not None
        on_thinking_chunk("Thinking...")
        on_chunk("Hello")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        with patch.object(ReplApp, "_scroll_if_pinned", autospec=True) as mock_scroll_if_pinned:
            prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
            prompt_input.text = "hi"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

        assert mock_scroll_if_pinned.call_args_list
        was_pinned_values = [call.args[-1] for call in mock_scroll_if_pinned.call_args_list]
        assert all(was_pinned_values)


async def test_streaming_updates_do_not_yank_the_scroll_when_the_user_has_scrolled_away() -> None:
    """See `test_streaming_updates_stay_pinned_to_the_bottom_when_the_user_is_at_the_bottom` for
    why this spies on `_scroll_if_pinned` rather than asserting on the viewport's actual scroll
    position.
    """
    mock_provider = MagicMock()
    first_chunk_sent = threading.Event()
    release_rest_of_turn = threading.Event()

    def fake_send_prompt(
        messages, system_prompt=None, model=None, session_id=None, reasoning=None, tools=None,
        drop_reasoning=False, on_chunk=None, on_thinking_chunk=None, on_reasoning_details=None,
        cache_mgmt_style="AUTOMATIC", cancel_event=None,
    ):
        assert on_thinking_chunk is not None
        assert on_chunk is not None
        on_thinking_chunk("First bit of thinking.")
        first_chunk_sent.set()
        release_rest_of_turn.wait(timeout=5)
        on_thinking_chunk(" More thinking, streamed in after the user scrolled away.")
        on_chunk("Hello")
        return _reply("Hello")

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test(size=(40, 6)) as pilot:
        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        for i in range(20):
            history.mount(Static(f"padding line {i}"))
        await pilot.pause()

        with patch.object(ReplApp, "_scroll_if_pinned", autospec=True) as mock_scroll_if_pinned:
            prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
            prompt_input.text = "hi"
            await pilot.press("enter")

            await _wait_until(pilot, first_chunk_sent.is_set)

            # The user scrolls up to reread earlier output while the rest of the turn streams in.
            # Waits on `_history_pinned_to_bottom` itself (what `_scroll_if_pinned` actually reads)
            # rather than `history.is_vertical_scroll_end`: the former is a reactive-watcher-
            # updated cache (see `ReplApp._on_history_scroll_changed`) that lags one message-pump
            # cycle behind the latter, which flips as soon as the deferred `scroll_home()` lands.
            history.scroll_home(animate=False)
            await _wait_until(pilot, lambda: not app._history_pinned_to_bottom)

            release_rest_of_turn.set()
            await app.workers.wait_for_complete()
            await pilot.pause()

        was_pinned_values = [call.args[-1] for call in mock_scroll_if_pinned.call_args_list]
        assert was_pinned_values
        assert not any(was_pinned_values[was_pinned_values.index(False):])


# --- workspace trust: bootstrap at startup and the "Trust workspace" command ---
