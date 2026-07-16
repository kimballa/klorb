# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.key_actions.KeyActionsMixin."""

import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from textual.containers import VerticalScroll
from textual.widgets import Static
from tui.conftest import _focused_id, _notice_texts, _session, _wait_until

from klorb.api_provider import ResponseAborted
from klorb.process_config import ProcessConfig
from klorb.tui.app import ReplApp
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID
from klorb.tui.mixins.key_actions import _CTRL_C_QUIT_WARNING, _INTERRUPTING_MESSAGE, CONFIG_MISSING_MESSAGE
from klorb.tui.widgets.prompt_input import PromptInput


async def test_on_mount_configures_tiktoken_cache_env() -> None:
    """`ReplApp.on_mount()` -- not `klorb.cli.main()` -- points tiktoken at the bundled cache
    for an interactive session, since by then the Textual app is actually running and its
    startup log message routes through the app's log / the session log file instead of
    leaking to raw stderr -- see
    docs/adrs/configure-tiktoken-cache-env-after-repl-app-mounts.md."""
    mock_provider = MagicMock()
    session = _session(mock_provider)
    app = ReplApp(session=session)

    with patch("klorb.tui.mixins.key_actions.configure_tiktoken_cache_env") as mock_configure_cache:
        async with app.run_test():
            pass

    mock_configure_cache.assert_called_once_with()


async def test_shows_config_missing_notice_when_user_config_file_absent(tmp_path: Path) -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    with patch(
        "klorb.tui.mixins.key_actions.user_config_path", return_value=tmp_path / "does-not-exist.json",
    ):
        async with app.run_test():
            history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
            notice = history.query_one(".notice", Static)
            assert str(notice.content) == CONFIG_MISSING_MESSAGE


async def test_omits_config_missing_notice_when_user_config_file_present(tmp_path: Path) -> None:
    config_path = tmp_path / "klorb-config.json"
    config_path.write_text("{}", encoding="utf-8")
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    with patch("klorb.tui.mixins.key_actions.user_config_path", return_value=config_path):
        async with app.run_test():
            history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
            assert len(history.query(".notice")) == 0


async def test_escape_shows_interrupting_notice() -> None:
    """Escape during a streaming turn mounts the `Interrupting…` notice so the user gets
    immediate confirmation the keypress landed — see `ReplApp._note_interrupt_requested`."""
    mock_provider = MagicMock()
    streaming_started = threading.Event()

    def fake_send_prompt(*args: Any, cancel_event: threading.Event | None = None, **kwargs: Any) -> Any:
        streaming_started.set()
        assert cancel_event is not None
        cancel_event.wait(timeout=5)
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).text = "hi"
        await pilot.press("enter")
        await _wait_until(pilot, streaming_started.is_set)

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert not history.query(".interrupting")
        await pilot.press("escape")
        await _wait_until(pilot, lambda: bool(history.query(".interrupting")))
        await app.workers.wait_for_complete()

        notices = history.query(".interrupting")
        assert len(notices) == 1
        assert str(notices.first(Static).render()) == _INTERRUPTING_MESSAGE


async def test_triple_ctrl_c_force_exits_a_stuck_turn(stub_force_exit: MagicMock) -> None:
    """Three Ctrl+C presses in a row, while a turn that ignores its cancel signal never unwinds,
    escalates to `_force_exit` — the escape for a worker that won't unwind. The first interrupts
    (turn stays in flight since it ignores the signal); the second, within the window, only
    shows the "press again to quit" notice rather than immediately force-exiting, since an
    interrupt is treated the same as a copy for streak-escalation purposes (see
    `ReplApp.action_interrupt`); the third actually force-exits."""
    mock_provider = MagicMock()
    streaming_started = threading.Event()
    release = threading.Event()

    def fake_send_prompt(*args: Any, cancel_event: threading.Event | None = None, **kwargs: Any) -> Any:
        streaming_started.set()
        # Deliberately ignore cancel_event: this stands in for a worker wedged inside a tool.
        release.wait(timeout=5)
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    try:
        async with app.run_test() as pilot:
            app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).text = "hi"
            await pilot.press("enter")
            await _wait_until(pilot, streaming_started.is_set)

            await pilot.press("ctrl+c")  # first: abort request; turn stays in flight
            await pilot.pause()
            assert app._turn_in_flight is True
            stub_force_exit.assert_not_called()

            await pilot.press("ctrl+c")  # second within window: only warns, doesn't force-exit
            await pilot.pause()
            stub_force_exit.assert_not_called()

            await pilot.press("ctrl+c")  # third within window: force-exit
            await _wait_until(pilot, lambda: stub_force_exit.called)
    finally:
        release.set()  # let the worker unwind so the harness can shut the app down

    stub_force_exit.assert_called()


async def test_watchdog_enabled_by_default_and_disabled_when_timeout_zero() -> None:
    """The liveness watchdog is on by default (`watchdog.timeout` defaults to 10s) and fully off
    when `watchdog.timeout <= 0`."""
    default_app = ReplApp(session=_session(MagicMock()))
    assert default_app._watchdog.enabled is True

    disabled_config = ProcessConfig(watchdog_timeout_seconds=0)
    disabled_app = ReplApp(session=_session(MagicMock()), process_config=disabled_config)
    assert disabled_app._watchdog.enabled is False
    async with disabled_app.run_test():
        # A disabled watchdog never starts a thread even after on_mount.
        assert disabled_app._watchdog._thread is None


async def test_shell_command_disables_input_and_ctrl_c_interrupts_it(tmp_path: Path) -> None:
    """A running shell command disables the input box (enforcing "only one at a time"), and
    Ctrl+C — which otherwise quits the app — kills the in-flight shell command instead.
    """
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))
    marker = tmp_path / "started"

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = f"!touch {marker}; sleep 5"
        await pilot.press("enter")

        await _wait_until(pilot, marker.exists)

        assert prompt_input.disabled is True
        assert app._shell_cancel_event is not None

        await pilot.press("ctrl+c")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        error_widget = history.query_one(".error", Static)
        assert "interrupted" in str(error_widget.content)
        assert prompt_input.disabled is False
        assert app._shell_cancel_event is None  # type: ignore[unreachable]
        assert app.is_running


async def test_first_idle_ctrl_c_only_warns() -> None:
    """A solitary Ctrl+C with nothing selected and nothing running shows the "press again to
    quit" notice rather than quitting immediately — see `ReplApp.action_interrupt`'s `"bare"`
    case and docs/specs/interrupt-and-liveness-watchdog.md's "Ctrl+C semantics" section."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.exit = MagicMock()  # type: ignore[method-assign]
        await pilot.press("ctrl+c")
        await pilot.pause()

        app.exit.assert_not_called()
        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert _CTRL_C_QUIT_WARNING in _notice_texts(app)
        assert history is not None


async def test_second_idle_ctrl_c_within_window_quits(stub_force_exit: MagicMock) -> None:
    """A second idle Ctrl+C within the window force-exits (`os._exit`, via `_force_exit`) rather
    than going through the polite `_quit_after_maybe_saving` save-prompt flow — see
    `ReplApp.action_interrupt`."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.exit = MagicMock()  # type: ignore[method-assign]
        await pilot.press("ctrl+c")
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()

        stub_force_exit.assert_called()
        app.exit.assert_not_called()  # force-exit bypasses the ordinary exit() path entirely


async def test_third_ctrl_c_quits_after_an_interrupt_then_a_warning(
    stub_force_exit: MagicMock,
) -> None:
    """When the first Ctrl+C in a rapid streak did something (interrupted a running shell
    command, here), the next idle Ctrl+C only warns rather than quitting immediately — it takes
    a *third* press within the window to force-exit. See `ReplApp.action_interrupt`'s streak
    bookkeeping (`_last_ctrl_c_kind`)."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "!sleep 5"
        await pilot.press("enter")
        await _wait_until(pilot, lambda: app._shell_cancel_event is not None)

        await pilot.press("ctrl+c")  # 1st: interrupts the running shell command
        await app.workers.wait_for_complete()
        await pilot.pause()
        kind_after_first_press = app._last_ctrl_c_kind
        assert kind_after_first_press == "interrupt"

        await pilot.press("ctrl+c")  # 2nd: idle now, but only warns (doesn't quit)
        await pilot.pause()
        kind_after_second_press = app._last_ctrl_c_kind
        assert kind_after_second_press == "bare"
        stub_force_exit.assert_not_called()

        await pilot.press("ctrl+c")  # 3rd: idle again within the window -- quits
        await pilot.pause()
        stub_force_exit.assert_called()


async def test_ctrl_c_aborts_an_in_flight_model_turn_instead_of_quitting() -> None:
    """Ctrl+C with a model turn in flight aborts the turn (like Escape) rather than quitting out
    from under it -- quitting a still-running turn is what used to strand its worker thread and
    hang process shutdown (see `_begin_exit`). The app stays running afterward."""
    mock_provider = MagicMock()
    streaming_started = threading.Event()

    def fake_send_prompt(*args: Any, cancel_event: threading.Event | None = None, **kwargs: Any) -> Any:
        assert cancel_event is not None
        streaming_started.set()
        cancel_event.wait(timeout=5)
        raise ResponseAborted()

    mock_provider.send_prompt.side_effect = fake_send_prompt
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.exit = MagicMock()  # type: ignore[method-assign]
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "what is 2+2?"
        await pilot.press("enter")

        await _wait_until(pilot, streaming_started.is_set)
        await pilot.press("ctrl+c")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.query_one(".interrupted", Static)
        assert prompt_input.disabled is False
        assert app._exit_requested is False
        app.exit.assert_not_called()


async def test_typing_while_history_is_focused_redirects_into_prompt_input() -> None:
    """A printable keystroke that reaches the App while the (non-editable) history scroll
    is focused should move focus to the message box and land the character there, rather
    than being silently dropped (see `ReplApp.on_key`)."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.focus()
        await pilot.pause()
        assert _focused_id(app) == HISTORY_ID

        await pilot.press("h", "i")
        await pilot.pause()

        assert _focused_id(app) == PROMPT_INPUT_ID
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        assert prompt_input.text == "hi"


async def test_typing_while_history_focused_is_not_redirected_when_input_disabled() -> None:
    """When the prompt input is disabled (e.g. an interaction panel is active), a printable
    keystroke on the history scroll should not be redirected into the (non-accepting) box."""
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))
    async with app.run_test() as pilot:
        await pilot.pause()
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.disabled = True
        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        history.focus()
        await pilot.pause()
        assert _focused_id(app) == HISTORY_ID

        await pilot.press("h")
        await pilot.pause()

        # Focus stayed on the history; the disabled box received nothing.
        assert _focused_id(app) == HISTORY_ID
        assert prompt_input.text == ""


# --- session persistence: save prompt on quit, auto-restore on startup ---
