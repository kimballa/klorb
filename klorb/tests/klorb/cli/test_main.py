# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli.main."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from klorb.cli.main import main
from klorb.hooks.hook_api import HookOutput
from klorb.logging_config import session_log_path
from klorb.openrouter import DEFAULT_MODEL
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig, ThinkingEffort


@pytest.fixture(autouse=True)
def stub_process_config() -> Iterator[MagicMock]:
    """Replace file-backed process config loading with a fresh default for every test in
    this module, so tests don't depend on `/etc`, `$HOME`, or the repo's own `cwd` being
    free of a stray `klorb-config.json`. Tests that care about the loading behavior itself
    patch or call `klorb.process_config.load_process_config` directly instead.
    """
    with patch("klorb.cli.main.load_process_config", return_value=ProcessConfig()) as mock_load:
        yield mock_load


def test_main_prints_prompt_response(capsys: pytest.CaptureFixture[str]) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "model reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "-m", "what is 2+2?"]):
            main()

    mock_session.run_one_shot.assert_called_once_with("what is 2+2?", on_chunk=mock.ANY)
    assert capsys.readouterr().out == "model reply\n"


def test_main_dispatches_onprocessstart_before_and_onprocessend_after_the_session(
    stub_process_config: MagicMock,
) -> None:
    """`onProcessStart` fires once `ProcessConfig` is resolved, before any `Session` exists;
    `onProcessEnd` fires once the process is otherwise done, even on the one-shot path where
    `Session.close()` already ran."""
    order: list[str] = []
    mock_session = MagicMock()

    def _run_one_shot(*args: object, **kwargs: object) -> str:
        order.append("run_one_shot")
        return "reply"

    mock_session.run_one_shot.side_effect = _run_one_shot

    def _session_side_effect(*args: object, **kwargs: object) -> MagicMock:
        order.append("Session")
        return mock_session

    mock_dispatcher = MagicMock()

    def _dispatch_side_effect(hook_name: str, hook_input: Any, **kwargs: object) -> HookOutput:
        order.append(hook_name)
        return HookOutput()

    mock_dispatcher.dispatch.side_effect = _dispatch_side_effect

    with patch("klorb.cli.main.HookDispatcher", return_value=mock_dispatcher) as mock_dispatcher_cls:
        with patch("klorb.cli.main.Session", side_effect=_session_side_effect):
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                main()

    mock_dispatcher_cls.assert_called_once_with(
        stub_process_config.return_value, api_provider=mock.ANY, model_registry=mock.ANY)
    assert order == ["onProcessStart", "Session", "run_one_shot", "onProcessEnd"]
    first_hook, second_hook = mock_dispatcher.dispatch.call_args_list
    assert first_hook.args[1].reason == "Startup"
    assert second_hook.args[1].reason == "Shutdown"
    assert second_hook.args[1].exit_status == 0


def test_main_dispatches_onprocessend_even_when_the_turn_raises(
    stub_process_config: MagicMock,
) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.side_effect = RuntimeError("boom")
    mock_dispatcher = MagicMock()
    mock_dispatcher.dispatch.return_value = HookOutput()
    with patch("klorb.cli.main.HookDispatcher", return_value=mock_dispatcher):
        with patch("klorb.cli.main.Session", return_value=mock_session):
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                with pytest.raises(RuntimeError):
                    main()

    hook_names = [call.args[0] for call in mock_dispatcher.dispatch.call_args_list]
    assert hook_names == ["onProcessStart", "onProcessEnd"]
    _, onprocessend_call = mock_dispatcher.dispatch.call_args_list
    assert onprocessend_call.args[1].exit_status == 1


def test_main_prints_onprocessstart_and_onprocessend_hook_log(
    stub_process_config: MagicMock, capsys: pytest.CaptureFixture[str],
) -> None:
    """`onProcessStart`/`onProcessEnd` fire before/after any `Session` exists, so `main()`
    prints their `HookOutput.log` directly rather than through `Session.deliver_notice`."""
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    mock_dispatcher = MagicMock()

    def _dispatch_side_effect(hook_name: str, hook_input: Any, **kwargs: object) -> HookOutput:
        return HookOutput(log=f"{hook_name} note")

    mock_dispatcher.dispatch.side_effect = _dispatch_side_effect

    with patch("klorb.cli.main.HookDispatcher", return_value=mock_dispatcher):
        with patch("klorb.cli.main.Session", return_value=mock_session):
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                main()

    out = capsys.readouterr().out
    assert "onProcessStart note" in out
    assert "onProcessEnd note" in out


def test_main_one_shot_registers_print_as_the_sessions_notice_handler(
    stub_process_config: MagicMock,
) -> None:
    """Headless execution has no TUI/webview to surface a hook's `HookOutput.log` in, so it
    registers `print` directly on the one-shot `Session`."""
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    mock_session.register_notice_handler.assert_called_once_with(print)


def test_main_configures_minimal_logging_immediately_after_load_dotenv() -> None:
    """`configure_minimal_logging()` must run right after `load_dotenv()`, before argument
    parsing or subcommand dispatch, so a log call anywhere in that window still reaches
    stderr."""
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "model reply"
    parent = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.load_dotenv", parent.load_dotenv):
            with patch("klorb.cli.main.configure_minimal_logging", parent.configure_minimal_logging):
                with patch("sys.argv", ["klorb", "-m", "what is 2+2?"]):
                    main()

    call_names = [name for name, _, _ in parent.mock_calls]
    assert call_names.index("load_dotenv") < call_names.index("configure_minimal_logging")


def test_main_accepts_long_message_flag() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "model reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "--message", "what is 2+2?"]):
            main()

    mock_session.run_one_shot.assert_called_once_with("what is 2+2?", on_chunk=mock.ANY)


def test_main_passes_custom_model() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "--model", "some/model", "-m", "hi"]):
            main()

    config = mock_session_cls.call_args.args[0]
    assert config.model == "some/model"
    mock_session.run_one_shot.assert_called_once_with("hi", on_chunk=mock.ANY)


def test_main_defaults_to_process_config_model_when_unset() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    config = mock_session_cls.call_args.args[0]
    assert config.model == DEFAULT_MODEL


def test_main_passes_process_config_base_url_to_provider(stub_process_config: MagicMock) -> None:
    process_config = ProcessConfig(openrouter_base_url="https://gateway.example.com/v1")
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.OpenRouterApiProvider") as mock_provider_cls:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                main()

    mock_provider_cls.assert_called_once_with(base_url="https://gateway.example.com/v1")


def test_main_passes_process_config_thinking_token_budgets_to_session(
    stub_process_config: MagicMock,
) -> None:
    custom_budgets: dict[ThinkingEffort, int] = {"low": 1, "medium": 2, "high": 3}
    process_config = ProcessConfig(thinking_token_budgets=custom_budgets)
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    assert mock_session_cls.call_args.kwargs["process_config"] is process_config


def test_main_passes_process_config_tool_call_limits_to_session(
    stub_process_config: MagicMock,
) -> None:
    process_config = ProcessConfig(session=SessionConfig(max_tool_calls_per_turn=3))
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    config = mock_session_cls.call_args.args[0]
    assert config.max_tool_calls_per_turn == 3


def test_main_passes_a_tool_registry_built_from_process_and_session_config(
    stub_process_config: MagicMock,
) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    sentinel_grants = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch(
            "klorb.cli.main.compute_root_session_grants", return_value=sentinel_grants,
        ) as mock_compute_grants:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                main()

    session_config = mock_session_cls.call_args.args[0]
    mock_compute_grants.assert_called_once_with(
        stub_process_config.return_value, session_config, session_config.role_name)
    assert mock_session_cls.call_args.kwargs["tool_registry"] is sentinel_grants.tool_registry
    assert (
        mock_session_cls.call_args.kwargs["effective_subagent_roles"]
        is sentinel_grants.effective_subagent_roles)


def test_main_passes_config_flag_path_to_process_config_loader(
    stub_process_config: MagicMock,
) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "--config", "/some/extra-config.json", "-m", "hi"]):
            main()

    stub_process_config.assert_called_once_with(
        config_flag_path=Path("/some/extra-config.json"), cwd=mock.ANY, workspace=mock.ANY)


def test_main_passes_no_config_flag_path_by_default(stub_process_config: MagicMock) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    stub_process_config.assert_called_once_with(config_flag_path=None, cwd=mock.ANY, workspace=mock.ANY)


def test_main_one_shot_config_is_not_interactive() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    config = mock_session_cls.call_args.args[0]
    assert config.interactive is False


def test_main_one_shot_defaults_permission_framework_to_deny() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    config = mock_session_cls.call_args.args[0]
    assert config.permission_framework == "deny"


def test_main_repl_defaults_permission_framework_to_ask() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.main.run_repl"):
            with patch("sys.argv", ["klorb"]):
                main()

    config = mock_session_cls.call_args.args[0]
    assert config.permission_framework == "ask"


def test_main_auto_approve_flag_sets_permission_framework_to_auto_one_shot() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-y", "-m", "hi"]):
            main()

    config = mock_session_cls.call_args.args[0]
    assert config.permission_framework == "auto"


def test_main_auto_approve_flag_sets_permission_framework_to_auto_interactively() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.main.run_repl"):
            with patch("sys.argv", ["klorb", "--auto-approve"]):
                main()

    config = mock_session_cls.call_args.args[0]
    assert config.permission_framework == "auto"


def test_main_log_tool_calls_flag_enables_process_config_setting(
    stub_process_config: MagicMock,
) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "--log-tool-calls", "-m", "hi"]):
            main()

    process_config = mock_session_cls.call_args.kwargs["process_config"]
    assert process_config.log_tool_calls is True


def test_main_defaults_log_tool_calls_to_process_config_when_flag_omitted(
    stub_process_config: MagicMock,
) -> None:
    stub_process_config.return_value = ProcessConfig(log_tool_calls=True)
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    process_config = mock_session_cls.call_args.kwargs["process_config"]
    assert process_config.log_tool_calls is True


def test_main_no_log_tool_calls_flag_overrides_config_true(
    stub_process_config: MagicMock,
) -> None:
    stub_process_config.return_value = ProcessConfig(log_tool_calls=True)
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "--no-log-tool-calls", "-m", "hi"]):
            main()

    process_config = mock_session_cls.call_args.kwargs["process_config"]
    assert process_config.log_tool_calls is False


def test_main_max_tool_calls_flags_override_process_config(stub_process_config: MagicMock) -> None:
    process_config = ProcessConfig(session=SessionConfig(max_tool_calls_per_turn=3))
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch(
            "sys.argv",
            ["klorb", "--max-tool-calls-per-turn", "5", "-m", "hi"],
        ):
            main()

    config = mock_session_cls.call_args.args[0]
    assert config.max_tool_calls_per_turn == 5


def test_main_max_tool_calls_flags_default_to_process_config_when_omitted(
    stub_process_config: MagicMock,
) -> None:
    process_config = ProcessConfig(session=SessionConfig(max_tool_calls_per_turn=3))
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    config = mock_session_cls.call_args.args[0]
    assert config.max_tool_calls_per_turn == 3


def test_main_starts_repl_when_no_prompt_given() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.main.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb"]):
                main()

    config = mock_session_cls.call_args.args[0]
    assert config.interactive is True
    mock_run_repl.assert_called_once_with(
        mock_session, process_config=mock.ANY, initial_message=None, session_log_enabled=True,
        trust_manager=mock.ANY, config_flag_path=None, skip_session_restore=False,
        quit_on_success=False)


def test_main_message_with_interactive_flag_starts_repl_with_initial_message() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.main.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb", "--interactive", "-m", "hi"]):
                main()

    config = mock_session_cls.call_args.args[0]
    assert config.interactive is True
    mock_run_repl.assert_called_once_with(
        mock_session, process_config=mock.ANY, initial_message="hi", session_log_enabled=True,
        trust_manager=mock.ANY, config_flag_path=None, skip_session_restore=True,
        quit_on_success=False)


def test_main_message_implies_new_session() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb", "--interactive", "-m", "hi"]):
                main()

    assert mock_run_repl.call_args.kwargs["skip_session_restore"] is True


def test_main_new_flag_skips_session_restore() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb", "--new"]):
                main()

    assert mock_run_repl.call_args.kwargs["skip_session_restore"] is True


def test_main_new_flag_defaults_to_false() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb"]):
                main()

    assert mock_run_repl.call_args.kwargs["skip_session_restore"] is False


def test_main_quit_on_success_flag_enables_it() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb", "--quit-on-success", "--interactive", "-m", "hi"]):
                main()

    assert mock_run_repl.call_args.kwargs["quit_on_success"] is True


def test_main_no_quit_on_success_flag_disables_it() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_repl") as mock_run_repl:
            with patch(
                "sys.argv",
                ["klorb", "--no-quit-on-success", "--interactive", "-m", "hi"],
            ):
                main()

    assert mock_run_repl.call_args.kwargs["quit_on_success"] is False


def test_main_no_message_and_explicit_no_interactive_errors() -> None:
    with patch("sys.argv", ["klorb", "--no-interactive"]):
        with pytest.raises(SystemExit):
            main()


def test_main_one_shot_skips_session_log_by_default() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.configure_logging") as mock_configure_logging:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                main()

    mock_configure_logging.assert_called_once_with(repl_mode=False, log_path=None)


def test_main_one_shot_writes_session_log_when_requested() -> None:
    mock_session = MagicMock()
    mock_session.id = "some-session-id"
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.configure_logging") as mock_configure_logging:
            with patch("sys.argv", ["klorb", "--session-log", "-m", "hi"]):
                main()

    mock_configure_logging.assert_called_once_with(
        repl_mode=False, log_path=session_log_path("some-session-id"))


def test_main_one_shot_configures_tiktoken_cache_env() -> None:
    """A one-shot prompt has no `ReplApp` to defer to, so `main()` itself must call
    `configure_tiktoken_cache_env()` (after `configure_logging()`, so its log message is
    visible)."""
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.configure_tiktoken_cache_env") as mock_configure_cache:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                main()

    mock_configure_cache.assert_called_once_with()


def test_main_repl_defers_tiktoken_cache_env_to_repl_app() -> None:
    """An interactive session's `configure_tiktoken_cache_env()` call is made by `ReplApp.
    on_mount()` instead, once the Textual app is actually running."""
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_repl"):
            with patch("klorb.cli.main.configure_tiktoken_cache_env") as mock_configure_cache:
                with patch("sys.argv", ["klorb"]):
                    main()

    mock_configure_cache.assert_not_called()


def test_main_repl_writes_session_log_by_default() -> None:
    mock_session = MagicMock()
    mock_session.id = "some-session-id"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_repl"):
            with patch("klorb.cli.main.configure_logging") as mock_configure_logging:
                with patch("sys.argv", ["klorb"]):
                    main()

    mock_configure_logging.assert_called_once_with(
        repl_mode=True, log_path=session_log_path("some-session-id"))


def test_main_repl_skips_session_log_when_disabled() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_repl"):
            with patch("klorb.cli.main.configure_logging") as mock_configure_logging:
                with patch("sys.argv", ["klorb", "--no-session-log"]):
                    main()

    mock_configure_logging.assert_called_once_with(repl_mode=True, log_path=None)


def test_main_repl_passes_session_log_enabled_false_when_disabled() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb", "--no-session-log"]):
                main()

    mock_run_repl.assert_called_once_with(
        mock_session, process_config=mock.ANY, initial_message=None, session_log_enabled=False,
        trust_manager=mock.ANY, config_flag_path=None, skip_session_restore=False,
        quit_on_success=False)


def test_main_one_shot_streams_incrementally_with_single_trailing_newline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_session = MagicMock()

    def fake_run_one_shot(prompt, on_chunk=None):
        on_chunk("Hel")
        on_chunk("lo")
        return "Hello"

    mock_session.run_one_shot.side_effect = fake_run_one_shot
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            main()

    assert capsys.readouterr().out == "Hello\n"


def test_main_dispatches_to_init_subcommand_when_argv1_is_init() -> None:
    with patch("klorb.cli.main.run_init_cli", return_value=0) as mock_run_init_cli:
        with patch("sys.argv", ["klorb", "init", "--user", "--force"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    mock_run_init_cli.assert_called_once_with(["--user", "--force"])
    assert exc_info.value.code == 0


def test_main_propagates_init_subcommand_failure_exit_code() -> None:
    with patch("klorb.cli.main.run_init_cli", return_value=1):
        with patch("sys.argv", ["klorb", "init"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    assert exc_info.value.code == 1


def test_main_does_not_treat_init_as_a_subcommand_unless_it_is_argv1() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_init_cli") as mock_run_init_cli:
            with patch("sys.argv", ["klorb", "-m", "init"]):
                main()

    mock_run_init_cli.assert_not_called()


def test_main_dispatches_to_system_prompt_subcommand_when_argv1_is_system_prompt() -> None:
    with patch("klorb.cli.main.run_system_prompt_cli", return_value=0) as mock_run:
        with patch("sys.argv", ["klorb", "system-prompt", "--role", "auditor"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    mock_run.assert_called_once_with(["--role", "auditor"])
    assert exc_info.value.code == 0


def test_main_propagates_system_prompt_subcommand_failure_exit_code() -> None:
    with patch("klorb.cli.main.run_system_prompt_cli", return_value=1):
        with patch("sys.argv", ["klorb", "system-prompt"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    assert exc_info.value.code == 1


def test_main_does_not_treat_system_prompt_as_a_subcommand_unless_it_is_argv1() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_system_prompt_cli") as mock_run:
            with patch("sys.argv", ["klorb", "-m", "system-prompt"]):
                main()

    mock_run.assert_not_called()


def test_main_dispatches_to_models_subcommand_when_argv1_is_models() -> None:
    with patch("klorb.cli.main.run_models_cli", return_value=0) as mock_run:
        with patch("sys.argv", ["klorb", "models", "--json"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    mock_run.assert_called_once_with(["--json"])
    assert exc_info.value.code == 0


def test_main_propagates_models_subcommand_failure_exit_code() -> None:
    with patch("klorb.cli.main.run_models_cli", return_value=1):
        with patch("sys.argv", ["klorb", "models"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    assert exc_info.value.code == 1


def test_main_does_not_treat_models_as_a_subcommand_unless_it_is_argv1() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_models_cli") as mock_run:
            with patch("sys.argv", ["klorb", "-m", "models"]):
                main()

    mock_run.assert_not_called()


def test_main_dispatches_to_show_config_subcommand_when_argv1_is_show_config() -> None:
    with patch("klorb.cli.main.run_show_config_cli", return_value=0) as mock_run:
        with patch("sys.argv", ["klorb", "show-config"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    mock_run.assert_called_once_with([])
    assert exc_info.value.code == 0


def test_main_propagates_show_config_subcommand_failure_exit_code() -> None:
    with patch("klorb.cli.main.run_show_config_cli", return_value=1):
        with patch("sys.argv", ["klorb", "show-config"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    assert exc_info.value.code == 1


def test_main_does_not_treat_show_config_as_a_subcommand_unless_it_is_argv1() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_show_config_cli") as mock_run:
            with patch("sys.argv", ["klorb", "-m", "show-config"]):
                main()

    mock_run.assert_not_called()


def test_main_dispatches_to_server_subcommand_when_argv1_is_server() -> None:
    with patch("klorb.cli.main.run_server_cli", return_value=0) as mock_run:
        with patch("sys.argv", ["klorb", "server"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    mock_run.assert_called_once_with([])
    assert exc_info.value.code == 0


def test_main_propagates_server_subcommand_failure_exit_code() -> None:
    with patch("klorb.cli.main.run_server_cli", return_value=1):
        with patch("sys.argv", ["klorb", "server"]):
            with pytest.raises(SystemExit) as exc_info:
                main()

    assert exc_info.value.code == 1


def test_main_does_not_treat_server_as_a_subcommand_unless_it_is_argv1() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.main.Session", return_value=mock_session):
        with patch("klorb.cli.main.run_server_cli") as mock_run:
            with patch("sys.argv", ["klorb", "-m", "server"]):
                main()

    mock_run.assert_not_called()


def test_version_flag_exits_with_version(capsys: pytest.CaptureFixture[str]) -> None:
    from klorb import __version__

    with patch("sys.argv", ["klorb", "--version"]):
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_short_version_flag_exits_with_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from klorb import __version__

    with patch("sys.argv", ["klorb", "-V"]):
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    assert __version__ in capsys.readouterr().out
