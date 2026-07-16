# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.app.ReplApp: core lifecycle, and cross-cutting flows spanning
more than one mixin."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.containers import VerticalScroll
from textual.geometry import Offset
from textual.screen import Screen
from textual.widgets import Markdown, Static
from tui.conftest import (
    TEST_SESSION_ID,
    _command_ask_ctx,
    _notice_texts,
    _PermissionAskTestApp,
    _repl_app_for_workspace,
    _reply,
    _session,
    _session_with_tools,
    _tool_call_reply,
)

from klorb import process_config as process_config_module
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tui.app import ReplApp, SelectionSafeScreen
from klorb.tui.constants import HISTORY_ID, PROMPT_INPUT_ID
from klorb.tui.panels.permission_ask_panel import (
    PERMISSION_ASK_MORE_ID,
    ExpandedCommandScreen,
    format_ask_context_body,
)
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.workspace import Workspace


async def test_selection_safe_screen_drops_hit_on_detached_widget() -> None:
    """A `MouseDown` hit-test that resolves to a detached (parent-less) widget — as happens
    when a click lands on a `MarkdownParagraph` mid streaming-remount — is dropped so Textual's
    text-selection code never dereferences the widget's `None` parent and crashes the app. See
    docs/adrs/drop-mousedown-on-detached-widget-to-avoid-selection-crash.md.
    """
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        screen = app.screen
        assert isinstance(screen, SelectionSafeScreen)

        detached = Markdown("x")  # never mounted, so its parent is None
        assert detached.parent is None
        with patch.object(
                Screen, "get_widget_and_offset_at", return_value=(detached, Offset(0, 0))):
            assert screen.get_widget_and_offset_at(3, 4) == (None, None)

        # An attached widget passes straight through, so normal selection still works.
        attached = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert attached.parent is not None
        with patch.object(
                Screen, "get_widget_and_offset_at", return_value=(attached, Offset(1, 2))):
            assert screen.get_widget_and_offset_at(3, 4) == (attached, Offset(1, 2))


async def test_submitting_a_prompt_shows_it_and_the_response() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "what is 2+2?"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = history.query(Static).exclude(".mascot").first()
        response_widget = history.query_one(Markdown)

        assert prompt_widget.content == "what is 2+2?"
        assert response_widget.source == "model reply"
        assert prompt_input.text == ""

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["model"] == "some/model"
    assert kwargs["session_id"] == TEST_SESSION_ID


async def test_select_model_updates_active_model_and_subtitle() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply("ok")
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.select_model("other/model")
        await pilot.pause()
        assert app.sub_title == "other/model"

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["model"] == "other/model"


async def test_set_thinking_enabled_updates_session_config() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_enabled(False)
        assert app._session.config.thinking_enabled is False

        app.set_thinking_enabled(True)
        assert app._session.config.thinking_enabled is True


async def test_set_thinking_effort_updates_session_config() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_effort("low")
        assert app._session.config.thinking_effort == "low"


async def test_select_model_also_updates_process_config_template() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.select_model("other/model")
        assert app._process_config.session.model == "other/model"


async def test_select_model_persists_to_the_user_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "klorb-config.json"
    config_path.write_text(json.dumps({"shell.command": "/bin/zsh"}), encoding="utf-8")
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    with patch("klorb.tui.app.user_config_path", return_value=config_path):
        async with app.run_test():
            app.select_model("other/model")

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["sessionDefaults"]["model"] == "other/model"
    assert written["shell.command"] == "/bin/zsh"


async def test_set_thinking_enabled_also_updates_process_config_template() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_enabled(False)
        assert app._process_config.session.thinking_enabled is False


async def test_set_thinking_enabled_persists_to_the_user_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "klorb-config.json"
    config_path.write_text(json.dumps({"shell.command": "/bin/zsh"}), encoding="utf-8")
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    with patch("klorb.tui.app.user_config_path", return_value=config_path):
        async with app.run_test():
            app.set_thinking_enabled(False)

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["sessionDefaults"]["thinking.enabled"] is False
    assert written["shell.command"] == "/bin/zsh"


async def test_set_thinking_effort_also_updates_process_config_template() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_effort("low")
        assert app._process_config.session.thinking_effort == "low"


async def test_set_thinking_effort_persists_to_the_user_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "klorb-config.json"
    config_path.write_text(json.dumps({"shell.command": "/bin/zsh"}), encoding="utf-8")
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    with patch("klorb.tui.app.user_config_path", return_value=config_path):
        async with app.run_test():
            app.set_thinking_effort("low")

    written = json.loads(config_path.read_text(encoding="utf-8"))
    assert written["sessionDefaults"]["thinking.effort"] == "low"
    assert written["shell.command"] == "/bin/zsh"


async def test_select_theme_updates_live_theme_and_process_config_template() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.select_theme("nord")
        assert app.theme == "nord"
        assert app._process_config.theme == "nord"


async def test_select_theme_persists_to_user_config() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.select_theme("nord")

    raw = json.loads(process_config_module.user_config_path().read_text(encoding="utf-8"))
    assert raw["ui.theme"] == "nord"


async def test_select_theme_announces_change_in_history_scroll() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.select_theme("nord")
        await pilot.pause()
        assert "Changed current theme to `nord`." in _notice_texts(app)


async def test_get_system_commands_excludes_builtin_theme_command() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        titles = {command.title for command in app.get_system_commands(app.screen)}
        assert "Theme" not in titles
        assert "Quit" in titles


async def test_constructor_applies_persisted_theme_from_process_config() -> None:
    mock_provider = MagicMock()
    process_config = ProcessConfig(theme="nord")
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test():
        assert app.theme == "nord"


async def test_constructor_ignores_unknown_persisted_theme() -> None:
    mock_provider = MagicMock()
    process_config = ProcessConfig(theme="not-a-real-theme")
    default_theme = ReplApp(session=_session(mock_provider)).theme
    app = ReplApp(session=_session(mock_provider), process_config=process_config)

    async with app.run_test():
        assert app.theme == default_theme


async def test_available_theme_names_returns_sorted_registered_themes() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        names = app.available_theme_names()
        assert names == sorted(names)
        assert "nord" in names


async def test_get_current_theme_name_reflects_active_theme() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.select_theme("nord")
        assert app.get_current_theme_name() == "nord"


async def test_get_thinking_effort_reflects_session_config() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test():
        app.set_thinking_effort("low")
        assert app.get_thinking_effort() == "low"


async def test_format_title_shows_workspace_path_model_and_thinking_effort() -> None:
    mock_provider = MagicMock()
    short_path = Path("/home/user/proj")
    config = SessionConfig(model="gpt-5", workspace=Workspace(path=short_path, trusted=True))
    session = Session(config, provider=mock_provider, session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test():
        app.set_thinking_enabled(True)
        app.set_thinking_effort("high")
        title = app.format_title(app.title, app.sub_title).plain
        assert title == f"{short_path} - gpt-5 (High)"


async def test_format_title_marks_an_untrusted_workspace_and_omits_effort_when_disabled() -> None:
    mock_provider = MagicMock()
    short_path = Path("/home/user/proj")
    config = SessionConfig(model="gpt-5", workspace=Workspace(path=short_path, trusted=False))
    session = Session(config, provider=mock_provider, session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test():
        app.set_thinking_enabled(False)
        title = app.format_title(app.title, app.sub_title).plain
        assert title == f"{short_path} (Untrusted) - gpt-5"


async def test_format_title_shortens_a_long_workspace_path_to_its_last_two_parts() -> None:
    mock_provider = MagicMock()
    long_path = Path("/some/very/deeply/nested/workspace/directory/tree/root")
    config = SessionConfig(model="gpt-5", workspace=Workspace(path=long_path, trusted=True))
    session = Session(config, provider=mock_provider, session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test():
        app.set_thinking_enabled(False)
        title = app.format_title(app.title, app.sub_title).plain
        assert title == ".../tree/root - gpt-5"


async def test_initial_message_is_submitted_as_first_turn() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.return_value = _reply()
    app = ReplApp(session=_session(mock_provider), initial_message="what is 2+2?")

    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompt_widget = history.query(Static).exclude(".mascot").first()
        response_widget = history.query_one(Markdown)

        assert prompt_widget.content == "what is 2+2?"
        assert response_widget.source == "model reply"

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        assert prompt_input.disabled is False

    _, kwargs = mock_provider.send_prompt.call_args
    assert kwargs["model"] == "some/model"
    assert kwargs["session_id"] == TEST_SESSION_ID


async def test_default_session_gets_a_tool_registry_discovering_built_in_tools() -> None:
    app = ReplApp()

    async with app.run_test():
        assert app._session.tool_registry is not None
        assert "ReadFile" in {tool.name() for tool in app._session.tool_registry.tools()}


async def test_ctrl_o_footer_label_reads_detail_then_hide() -> None:
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        _tool_call_reply([("call_1", "echo", '{"message": "hi"}')]),
        _reply("final answer"),
    ]
    session = _session_with_tools(mock_provider, SessionConfig(model="some/model"))
    app = ReplApp(session=session)

    async with app.run_test() as pilot:
        assert app.active_bindings["ctrl+o"].binding.description == "Detail"

        prompt_input = app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput)
        prompt_input.text = "please echo"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.active_bindings["ctrl+o"].binding.description == "Hide"

        await pilot.press("ctrl+o")
        await pilot.pause()
        assert app.active_bindings["ctrl+o"].binding.description == "Detail"


def test_format_ask_context_body_leads_with_intent_when_set() -> None:
    ctx = _command_ask_ctx("grep -n _wait_until tests/", reason="run command: grep", intent="List call sites")

    body = format_ask_context_body(ctx)

    assert body == "Intent: List call sites\ngrep -n _wait_until tests/\nrun command: grep"


def test_format_ask_context_body_omits_intent_line_when_unset() -> None:
    ctx = _command_ask_ctx("echo hi", reason="run command: echo hi")

    body = format_ask_context_body(ctx)

    assert body == "echo hi\nrun command: echo hi"


async def test_expand_shows_the_full_compound_command_not_just_this_items_own_command() -> None:
    """Even though the per-item preview shows only this item's own command (item_command_text),
    expanding it must still reveal the *whole* compound command_text -- that's the entire point
    of `[more...]` existing for a short, already-fully-visible-looking item preview."""
    app = _PermissionAskTestApp(_command_ask_ctx(
        "echo $SHELL; echo $HOME", is_compound=True, item_command_text="echo $SHELL"))

    async with app.run_test() as pilot:
        await pilot.press("plus")
        await pilot.pause()

        assert isinstance(app.screen, ExpandedCommandScreen)
        assert str(app.screen.query_one(Static).render()) == "echo $SHELL; echo $HOME"


async def test_clicking_the_more_indicator_opens_expanded_command_screen() -> None:
    long_command = "\n".join(f"line {i}" for i in range(1, 21))
    app = _PermissionAskTestApp(_command_ask_ctx(long_command))

    async with app.run_test() as pilot:
        await pilot.click(f"#{PERMISSION_ASK_MORE_ID}")
        await pilot.pause()

        assert isinstance(app.screen, ExpandedCommandScreen)


async def test_no_trust_manager_skips_bootstrap_and_announcement_entirely(tmp_path: Path) -> None:
    workspace = Workspace(path=tmp_path)
    app = _repl_app_for_workspace(workspace, trust_manager=None)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert _notice_texts(app) == []


async def test_quit_skips_save_prompt_with_no_trust_manager() -> None:
    mock_provider = MagicMock()
    app = ReplApp(session=_session(mock_provider))

    async with app.run_test() as pilot:
        app.exit = MagicMock()  # type: ignore[method-assign]
        await pilot.press("ctrl+q")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        app.exit.assert_called_once()
