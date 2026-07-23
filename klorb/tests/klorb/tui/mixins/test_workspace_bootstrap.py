# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tui.mixins.workspace_bootstrap.WorkspaceBootstrapMixin."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Static
from tui.conftest import (
    TEST_SESSION_ID,
    _focused_id,
    _isolated_data_dir,
    _notice_texts,
    _repl_app_for_workspace,
    _sample_message,
    _wait_until,
)

from klorb.message import Message, ToolCallRequest
from klorb.process_config import CONFIG_SCHEMA_NAME, SESSION_DEFAULTS_KEY, project_config_path
from klorb.schema_envelope import read_versioned_json
from klorb.session import Session, SessionConfig
from klorb.tools.skill.catalog import get_skill_catalog_registry
from klorb.tui.app import ReplApp
from klorb.tui.commands.trust_commands import TRUST_WORKSPACE_LABEL
from klorb.tui.constants import HISTORY_ID, NEW_SESSION_LABEL, PROMPT_INPUT_ID, SESSION_NAME_ID
from klorb.tui.panels.confirm_screen import (
    CONFIRM_NO_ID,
    CONFIRM_YES_ID,
    QUIT_CANCEL_ID,
    QUIT_DISCARD_ID,
    QUIT_SAVE_ID,
    ConfirmScreen,
    SaveOnQuitScreen,
)
from klorb.tui.widgets.palette import PALETTE_PREFIX
from klorb.tui.widgets.prompt_input import PromptInput
from klorb.tui.widgets.tool_call_widgets import ToolCallStatic
from klorb.workspace import TrustManager, Workspace
from klorb.workspace.last_session import read_last_session, write_last_session
from klorb.workspace.workspace_init import write_initial_project_config


async def test_trusting_a_workspace_refreshes_the_header_title(tmp_path: Path) -> None:
    mock_provider = MagicMock()
    config = SessionConfig(model="gpt-5", workspace=Workspace(path=tmp_path, trusted=False))
    session = Session(config, provider=mock_provider, session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test():
        app.set_thinking_enabled(False)
        assert "(Untrusted)" in app.format_title(app.title, app.sub_title).plain

        app._apply_workspace_config(
            session.config.workspace.model_copy(update={"trusted": True}))
        assert "(Untrusted)" not in app.format_title(app.title, app.sub_title).plain


async def test_trusting_a_workspace_reloads_the_skill_catalog(tmp_path: Path) -> None:
    """A workspace-tier skill invisible to the process-wide catalog while untrusted becomes
    resolvable immediately after `_apply_workspace_config()` applies a newly-trusted workspace,
    rather than staying invisible until an explicit `>Reload skills`."""
    skill_dir = tmp_path / ".klorb" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ndescription: d\n---\n")

    mock_provider = MagicMock()
    config = SessionConfig(model="gpt-5", workspace=Workspace(path=tmp_path, trusted=False))
    session = Session(config, provider=mock_provider, session_id=TEST_SESSION_ID)
    app = ReplApp(session=session)

    async with app.run_test():
        app.set_thinking_enabled(False)
        # Simulate a turn that built the catalog before the workspace was trusted, exactly like
        # a real skill Tool's `registry.ensure_from_context()` would on an early turn.
        get_skill_catalog_registry().ensure(
            workspace_root=tmp_path, workspace_trusted=False, claude_skills_compat=False)
        assert get_skill_catalog_registry().canonical().get(("workspace", "my-skill")) is None

        app._apply_workspace_config(
            session.config.workspace.model_copy(update={"trusted": True}))
        assert get_skill_catalog_registry().canonical().get(("workspace", "my-skill")) is not None


async def test_registered_trusted_workspace_announces_and_shows_no_modal(tmp_path: Path) -> None:
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert f"Working in project: {tmp_path}" in _notice_texts(app)


async def test_registered_untrusted_workspace_announces_not_trusted_with_no_modal(tmp_path: Path) -> None:
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1
        notices = _notice_texts(app)
        assert any(
            f"The workspace at {tmp_path} is not trusted" in notice and TRUST_WORKSPACE_LABEL in notice
            for notice in notices)


async def test_bootstrap_open_as_project_and_trust_registers_and_writes_config(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=project_root)
    app = _repl_app_for_workspace(workspace, trust_manager, model="burned/model")

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # "Open as project?" -> Yes
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # "Do you trust...?" -> Yes
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert f"Working in project: {project_root}" in _notice_texts(app)
        assert app._session.config.workspace.trusted is True

    resolved = trust_manager.resolve_workspace(project_root)
    assert resolved.is_project is True
    assert resolved.trusted is True

    raw = read_versioned_json(project_config_path(project_root), expected_schema_name=CONFIG_SCHEMA_NAME)
    session_defaults = raw[SESSION_DEFAULTS_KEY]
    assert session_defaults["model"] == "burned/model"
    assert session_defaults["readDirs"]["allow"] == [str(project_root)]
    assert session_defaults["writeDirs"]["allow"] == [str(project_root)]


async def test_bootstrap_keeps_an_existing_project_config_file(tmp_path: Path) -> None:
    """A workspace that already ships its own `.klorb/klorb-config.json` (e.g. a downloaded
    repository) must not have it clobbered with the starter template when the user opens it
    as a project and trusts it."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    write_initial_project_config(project_root, "shipped/model", trusted=True)
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=project_root)
    app = _repl_app_for_workspace(workspace, trust_manager, model="burned/model")

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # "Open as project?" -> Yes
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # "Do you trust...?" -> Yes
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert f"Working in project: {project_root}" in _notice_texts(app)
        assert app._session.config.workspace.trusted is True

    resolved = trust_manager.resolve_workspace(project_root)
    assert resolved.is_project is True
    assert resolved.trusted is True

    raw = read_versioned_json(project_config_path(project_root), expected_schema_name=CONFIG_SCHEMA_NAME)
    assert raw[SESSION_DEFAULTS_KEY]["model"] == "shipped/model"


async def test_confirm_screen_arrow_keys_move_focus_between_buttons(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=project_root)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)

        assert _focused_id(app) == CONFIRM_YES_ID
        await pilot.press("right")
        assert _focused_id(app) == CONFIRM_NO_ID
        await pilot.press("left")
        assert _focused_id(app) == CONFIRM_YES_ID

        await pilot.press("escape")
        await pilot.pause()


async def test_bootstrap_declining_project_but_trusting_keeps_it_in_memory_only(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=project_root)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_NO_ID}")  # "Open as project?" -> No
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # "Do you trust...?" -> Yes
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.id is None
        assert app._session.config.workspace.is_project is False
        assert app._session.config.workspace.trusted is True

    assert trust_manager.resolve_workspace(project_root).id is None
    assert not project_config_path(project_root).is_file()


async def test_bootstrap_declining_both_stays_unregistered_and_untrusted(
    tmp_path: Path,
) -> None:
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=tmp_path)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_NO_ID}")  # "Open as project?" -> No
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_NO_ID}")  # "Do you trust...?" -> No
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.trusted is False
        assert any("not trusted" in notice for notice in _notice_texts(app))

    assert trust_manager.resolve_workspace(tmp_path).id is None
    assert not project_config_path(tmp_path).is_file()


async def test_trust_workspace_command_persists_and_offers_config_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.screen_stack) == 1  # already registered: no bootstrap modal at mount

        # Set directly rather than typed key-by-key (see
        # test_enter_with_no_matching_palette_option_submits_as_a_plain_prompt for why).
        app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).text = f"{PALETTE_PREFIX}trust"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # confirm trust
        await pilot.pause()

        # tmp_path has no config file yet and is a registered project, so a second modal offers
        # to write one from the live session's current settings.
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # yes, initialize
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.trusted is True
        assert any("Trusted workspace" in notice for notice in _notice_texts(app))

    assert trust_manager.resolve_workspace(tmp_path).trusted is True
    assert project_config_path(tmp_path).is_file()


async def test_trust_workspace_command_declining_config_init_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()

        # Set directly rather than typed key-by-key (see
        # test_enter_with_no_matching_palette_option_submits_as_a_plain_prompt for why).
        app.query_one(f"#{PROMPT_INPUT_ID}", PromptInput).text = f"{PALETTE_PREFIX}trust"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # confirm trust
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_NO_ID}")  # decline config init
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.trusted is True

    assert not project_config_path(tmp_path).is_file()


async def test_trust_workspace_for_non_project_workspace_skips_config_init_prompt(
    tmp_path: Path,
) -> None:
    """A non-project (never registered) workspace can only reach "already resolved, untrusted"
    mid-session -- a fresh mount always treats `workspace.id is None` as "never resolved" and
    runs the full bootstrap (see the tests above). Simulated here by constructing the app with
    no `TrustManager` (so mounting doesn't itself trigger a fresh bootstrap) and attaching one
    afterward, then driving `trust_workspace()` directly as a background task rather than via
    the palette, so this test can focus purely on the "not a project" branch.
    """
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = Workspace(path=tmp_path, is_project=False, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager=None)

    async with app.run_test() as pilot:
        await pilot.pause()
        app._trust_manager = trust_manager

        app.trust_workspace()
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{CONFIRM_YES_ID}")  # confirm trust
        await pilot.pause()

        await _wait_until(pilot, lambda: len(app.screen_stack) == 1)
        assert app._session.config.workspace.trusted is True

    assert not project_config_path(tmp_path).is_file()


async def test_quit_skips_save_prompt_for_untrusted_workspace(tmp_path: Path) -> None:
    trust_manager = TrustManager(path=tmp_path / "data" / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=False)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.exit = MagicMock()  # type: ignore[method-assign]
        await pilot.press("ctrl+q")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1  # no ConfirmScreen was ever pushed
        app.exit.assert_called_once()


async def test_quit_prompts_and_writes_last_session_on_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager, model="save/model")

    async with app.run_test() as pilot:
        await pilot.pause()
        app._session.load_messages([_sample_message("hi")])
        app.exit = MagicMock()  # type: ignore[method-assign]

        await pilot.press("ctrl+q")
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, SaveOnQuitScreen)
        await pilot.click(f"#{QUIT_SAVE_ID}")
        await app.workers.wait_for_complete()
        await pilot.pause()

        app.exit.assert_called_once()

    state = read_last_session(workspace)
    assert state is not None
    assert state.config.model == "save/model"
    assert [m.content for m in state.messages] == ["hi"]


async def test_quit_declining_save_prompt_does_not_write_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager)
    app._session.load_messages([_sample_message("hi")])

    async with app.run_test() as pilot:
        await pilot.pause()
        app.exit = MagicMock()  # type: ignore[method-assign]

        await pilot.press("ctrl+q")
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        await pilot.click(f"#{QUIT_DISCARD_ID}")
        await app.workers.wait_for_complete()
        await pilot.pause()

        app.exit.assert_called_once()

    assert read_last_session(workspace) is None


async def test_quit_cancel_button_aborts_the_quit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `SaveOnQuitScreen`'s "Cancel" button dismisses the modal without quitting or saving —
    the session keeps running exactly as before."""
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager)
    app._session.load_messages([_sample_message("hi")])

    async with app.run_test() as pilot:
        await pilot.pause()
        app.exit = MagicMock()  # type: ignore[method-assign]

        await pilot.press("ctrl+q")
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, SaveOnQuitScreen)
        await pilot.click(f"#{QUIT_CANCEL_ID}")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        app.exit.assert_not_called()

    assert read_last_session(workspace) is None


async def test_quit_escape_dismisses_the_save_prompt_as_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Escape on the `SaveOnQuitScreen` is equivalent to clicking "Cancel" — it aborts the quit
    rather than declining to save (which is what "No" does)."""
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager)
    app._session.load_messages([_sample_message("hi")])

    async with app.run_test() as pilot:
        await pilot.pause()
        app.exit = MagicMock()  # type: ignore[method-assign]

        await pilot.press("ctrl+q")
        await _wait_until(pilot, lambda: len(app.screen_stack) == 2)
        assert isinstance(app.screen, SaveOnQuitScreen)
        await pilot.press("escape")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert len(app.screen_stack) == 1
        app.exit.assert_not_called()

    assert read_last_session(workspace) is None


async def test_quit_skips_save_prompt_with_no_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When there are no messages in the session, the save prompt should be skipped."""
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager)

    async with app.run_test() as pilot:
        await pilot.pause()
        app.exit = MagicMock()  # type: ignore[method-assign]

        await pilot.press("ctrl+q")
        await app.workers.wait_for_complete()
        await pilot.pause()

        # No ConfirmScreen was pushed because there are no messages
        assert len(app.screen_stack) == 1
        app.exit.assert_called_once()

    assert read_last_session(workspace) is None


async def test_restores_previous_session_config_and_messages_on_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    write_last_session(
        workspace, SessionConfig(model="restored/model", workspace=workspace),
        [_sample_message("earlier prompt", "user"),
         _sample_message("earlier reply", "assistant")])

    app = _repl_app_for_workspace(workspace, trust_manager, model="fresh/model")
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app._session.config.model == "restored/model"
        assert [m.content for m in app._session.messages] == ["earlier prompt", "earlier reply"]

        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        prompts = list(history.query(".prompt"))
        assert any(isinstance(w, Static) and str(w.content) == "earlier prompt" for w in prompts)
        assert len(list(history.query(".response"))) == 1
        assert any("Restored previous session" in notice for notice in _notice_texts(app))


async def test_restore_skipped_when_no_saved_session_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    app = _repl_app_for_workspace(workspace, trust_manager, model="fresh/model")

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._session.config.model == "fresh/model"
        assert app._session.messages == []


async def test_restores_tool_call_history_as_a_tool_call_widget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    tool_use = Message(
        content="", role="tool_use", num_tokens=1, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 0),
        tool_calls=[ToolCallRequest(id="call-1", name="SampleTool", arguments='{"x": 1}')])
    tool_response = Message(
        content="42", role="tool_response", num_tokens=1, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 1), tool_call_id="call-1")
    write_last_session(workspace, SessionConfig(workspace=workspace), [tool_use, tool_response])

    app = _repl_app_for_workspace(workspace, trust_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(list(history.query(ToolCallStatic))) == 1


async def test_restores_tool_call_history_from_a_structured_response_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `tool_response.content` saved as a `klorb.tools.response_envelope.
    ToolResponseEnvelope`'s wire JSON (`Session._run_tool_calls`'s persisted shape) restores the
    same way a legacy bare-string/`"Error: ..."` one does -- see
    `klorb.tui.mixins.rendering.ReplApp._render_restored_tool_call`."""
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    tool_use = Message(
        content="", role="tool_use", num_tokens=1, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 0),
        tool_calls=[ToolCallRequest(id="call-1", name="SampleTool", arguments='{"x": 1}')])
    envelope_content = json.dumps({
        "is_error": True, "is_retryable": False, "error_category": "business_logic",
        "error_message": "boom",
    })
    tool_response = Message(
        content=envelope_content, role="tool_response", num_tokens=1, processing_state="complete",
        timestamp=datetime(2026, 7, 12, 0, 0, 1), tool_call_id="call-1")
    write_last_session(workspace, SessionConfig(workspace=workspace), [tool_use, tool_response])

    app = _repl_app_for_workspace(workspace, trust_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        history = app.query_one(f"#{HISTORY_ID}", VerticalScroll)
        assert len(list(history.query(ToolCallStatic))) == 1


async def test_restore_restores_session_id_and_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    write_last_session(
        workspace,
        SessionConfig(model="restored/model", workspace=workspace),
        [_sample_message("earlier prompt", "user")],
        session_id="2026-07-19-01-50-fix-auth",
        session_name="Fix auth token refresh bug")

    app = _repl_app_for_workspace(workspace, trust_manager, model="fresh/model")
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app._session.id == "2026-07-19-01-50-fix-auth"
        assert app._session.name == "Fix auth token refresh bug"
        assert not app._session.session_naming_pending

        session_name_widget = app.query_one(f"#{SESSION_NAME_ID}", Static)
        assert "Fix auth token refresh bug" in str(session_name_widget.content)


async def test_restore_without_name_keeps_naming_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolated_data_dir(tmp_path, monkeypatch)
    trust_manager = TrustManager(path=tmp_path / "projects.json")
    workspace = trust_manager.register_project(tmp_path, trusted=True)
    # Write a session without session_name (simulates older save format)
    write_last_session(
        workspace,
        SessionConfig(model="restored/model", workspace=workspace),
        [_sample_message("earlier prompt", "user")])

    app = _repl_app_for_workspace(workspace, trust_manager, model="fresh/model")
    async with app.run_test() as pilot:
        await pilot.pause()

        assert app._session.name is None
        assert app._session.session_naming_pending

        session_name_widget = app.query_one(f"#{SESSION_NAME_ID}", Static)
        assert str(session_name_widget.content) == NEW_SESSION_LABEL


# --- session persistence: saved on crash exit (run_repl -> _handle_repl_crash) ---
