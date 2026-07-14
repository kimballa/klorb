# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli."""

import json
from collections.abc import Iterator
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from klorb import cli
from klorb import token_estimate as token_estimate_module
from klorb.klorb_init import InitError
from klorb.logging_config import session_log_path
from klorb.models.model import Model
from klorb.models.openrouter_pricing import ModelPricing
from klorb.openrouter import DEFAULT_MODEL
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig, ThinkingEffort
from klorb.workspace import Workspace
from klorb.workspace import trust_manager as trust_manager_module


@pytest.fixture(autouse=True)
def _isolate_projects_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `klorb.workspace.trust_manager.projects_path()` (and so the real `TrustManager()`
    `cli.main()` constructs) and `klorb.token_estimate.tiktoken_cache_target_dir()` (and so
    `cli.main()`'s `configure_tiktoken_cache_env()` call) at an empty location under
    `tmp_path`, so no test in this module reads or writes the developer's own
    `$KLORB_DATA_DIR/projects.json` or `$KLORB_DATA_DIR/tiktoken-cache/`."""
    monkeypatch.setattr(trust_manager_module, "KLORB_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(token_estimate_module, "KLORB_DATA_DIR", tmp_path / "data")


@pytest.fixture(autouse=True)
def stub_process_config() -> Iterator[MagicMock]:
    """Replace file-backed process config loading with a fresh default for every test in
    this module, so tests don't depend on `/etc`, `$HOME`, or the repo's own `cwd` being
    free of a stray `klorb-config.json`. Tests that care about the loading behavior itself
    patch or call `klorb.process_config.load_process_config` directly instead.
    """
    with patch("klorb.cli.load_process_config", return_value=ProcessConfig()) as mock_load:
        yield mock_load


def test_main_prints_prompt_response(capsys: pytest.CaptureFixture[str]) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "model reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "-m", "what is 2+2?"]):
            cli.main()

    mock_session.run_one_shot.assert_called_once_with("what is 2+2?", on_chunk=mock.ANY)
    assert capsys.readouterr().out == "model reply\n"


def test_main_accepts_long_message_flag() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "model reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "--message", "what is 2+2?"]):
            cli.main()

    mock_session.run_one_shot.assert_called_once_with("what is 2+2?", on_chunk=mock.ANY)


def test_main_passes_custom_model() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "--model", "some/model", "-m", "hi"]):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.model == "some/model"
    mock_session.run_one_shot.assert_called_once_with("hi", on_chunk=mock.ANY)


def test_main_defaults_to_process_config_model_when_unset() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.model == DEFAULT_MODEL


def test_main_passes_process_config_base_url_to_provider(stub_process_config: MagicMock) -> None:
    process_config = ProcessConfig(openrouter_base_url="https://gateway.example.com/v1")
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.OpenRouterApiProvider") as mock_provider_cls:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                cli.main()

    mock_provider_cls.assert_called_once_with(base_url="https://gateway.example.com/v1")


def test_main_passes_process_config_thinking_token_budgets_to_session(
    stub_process_config: MagicMock,
) -> None:
    custom_budgets: dict[ThinkingEffort, int] = {"low": 1, "medium": 2, "high": 3}
    process_config = ProcessConfig(thinking_token_budgets=custom_budgets)
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    assert mock_session_cls.call_args.kwargs["process_config"] is process_config


def test_main_passes_process_config_tool_call_limits_to_session(
    stub_process_config: MagicMock,
) -> None:
    process_config = ProcessConfig(
        session=SessionConfig(max_tool_calls_per_turn=3, max_tool_calls_per_session=9))
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.max_tool_calls_per_turn == 3
    assert config.max_tool_calls_per_session == 9


def test_main_passes_a_tool_registry_built_from_process_and_session_config(
    stub_process_config: MagicMock,
) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    sentinel_registry = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.ToolRegistry", return_value=sentinel_registry) as mock_registry_cls:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                cli.main()

    session_config = mock_session_cls.call_args.args[0]
    mock_registry_cls.assert_called_once_with(stub_process_config.return_value, session_config)
    assert mock_session_cls.call_args.kwargs["tool_registry"] is sentinel_registry


def test_main_passes_config_flag_path_to_process_config_loader(
    stub_process_config: MagicMock,
) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "--config", "/some/extra-config.json", "-m", "hi"]):
            cli.main()

    stub_process_config.assert_called_once_with(
        config_flag_path=Path("/some/extra-config.json"), cwd=mock.ANY, workspace=mock.ANY)


def test_main_passes_no_config_flag_path_by_default(stub_process_config: MagicMock) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    stub_process_config.assert_called_once_with(config_flag_path=None, cwd=mock.ANY, workspace=mock.ANY)


def test_main_one_shot_config_is_not_interactive() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.interactive is False


def test_main_one_shot_defaults_permission_framework_to_deny() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.permission_framework == "deny"


def test_main_repl_defaults_permission_framework_to_ask() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.run_repl"):
            with patch("sys.argv", ["klorb"]):
                cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.permission_framework == "ask"


def test_main_auto_approve_flag_sets_permission_framework_to_auto_one_shot() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-y", "-m", "hi"]):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.permission_framework == "auto"


def test_main_auto_approve_flag_sets_permission_framework_to_auto_interactively() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.run_repl"):
            with patch("sys.argv", ["klorb", "--auto-approve"]):
                cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.permission_framework == "auto"


def test_main_log_tool_calls_flag_enables_process_config_setting(
    stub_process_config: MagicMock,
) -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "--log-tool-calls", "-m", "hi"]):
            cli.main()

    process_config = mock_session_cls.call_args.kwargs["process_config"]
    assert process_config.log_tool_calls is True


def test_main_defaults_log_tool_calls_to_process_config_when_flag_omitted(
    stub_process_config: MagicMock,
) -> None:
    stub_process_config.return_value = ProcessConfig(log_tool_calls=True)
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    process_config = mock_session_cls.call_args.kwargs["process_config"]
    assert process_config.log_tool_calls is True


def test_main_no_log_tool_calls_flag_overrides_config_true(
    stub_process_config: MagicMock,
) -> None:
    stub_process_config.return_value = ProcessConfig(log_tool_calls=True)
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "--no-log-tool-calls", "-m", "hi"]):
            cli.main()

    process_config = mock_session_cls.call_args.kwargs["process_config"]
    assert process_config.log_tool_calls is False


def test_main_max_tool_calls_flags_override_process_config(stub_process_config: MagicMock) -> None:
    process_config = ProcessConfig(
        session=SessionConfig(max_tool_calls_per_turn=3, max_tool_calls_per_session=9))
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch(
            "sys.argv",
            ["klorb", "--max-tool-calls-per-turn", "5", "--max-tool-calls-per-session", "20", "-m", "hi"],
        ):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.max_tool_calls_per_turn == 5
    assert config.max_tool_calls_per_session == 20


def test_main_max_tool_calls_flags_default_to_process_config_when_omitted(
    stub_process_config: MagicMock,
) -> None:
    process_config = ProcessConfig(
        session=SessionConfig(max_tool_calls_per_turn=3, max_tool_calls_per_session=9))
    stub_process_config.return_value = process_config
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.max_tool_calls_per_turn == 3
    assert config.max_tool_calls_per_session == 9


def test_main_starts_repl_when_no_prompt_given() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb"]):
                cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.interactive is True
    mock_run_repl.assert_called_once_with(
        mock_session, process_config=mock.ANY, initial_message=None, session_log_enabled=True,
        trust_manager=mock.ANY, config_flag_path=None)


def test_main_message_with_interactive_flag_starts_repl_with_initial_message() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session) as mock_session_cls:
        with patch("klorb.cli.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb", "--interactive", "-m", "hi"]):
                cli.main()

    config = mock_session_cls.call_args.args[0]
    assert config.interactive is True
    mock_run_repl.assert_called_once_with(
        mock_session, process_config=mock.ANY, initial_message="hi", session_log_enabled=True,
        trust_manager=mock.ANY, config_flag_path=None)


def test_main_no_message_and_explicit_no_interactive_errors() -> None:
    with patch("sys.argv", ["klorb", "--no-interactive"]):
        with pytest.raises(SystemExit):
            cli.main()


def test_main_one_shot_skips_session_log_by_default() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.configure_logging") as mock_configure_logging:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                cli.main()

    mock_configure_logging.assert_called_once_with(repl_mode=False, log_path=None)


def test_main_one_shot_writes_session_log_when_requested() -> None:
    mock_session = MagicMock()
    mock_session.id = "some-session-id"
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.configure_logging") as mock_configure_logging:
            with patch("sys.argv", ["klorb", "--session-log", "-m", "hi"]):
                cli.main()

    mock_configure_logging.assert_called_once_with(
        repl_mode=False, log_path=session_log_path("some-session-id"))


def test_main_one_shot_configures_tiktoken_cache_env() -> None:
    """A one-shot prompt has no `ReplApp` to defer to, so `main()` itself must call
    `configure_tiktoken_cache_env()` (after `configure_logging()`, so its log message is
    visible) -- see docs/adrs/configure-tiktoken-cache-env-after-repl-app-mounts.md."""
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.configure_tiktoken_cache_env") as mock_configure_cache:
            with patch("sys.argv", ["klorb", "-m", "hi"]):
                cli.main()

    mock_configure_cache.assert_called_once_with()


def test_main_repl_defers_tiktoken_cache_env_to_repl_app() -> None:
    """An interactive session's `configure_tiktoken_cache_env()` call is made by `ReplApp.
    on_mount()` instead, once the Textual app is actually running -- not by `main()` -- see
    docs/adrs/configure-tiktoken-cache-env-after-repl-app-mounts.md."""
    mock_session = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.run_repl"):
            with patch("klorb.cli.configure_tiktoken_cache_env") as mock_configure_cache:
                with patch("sys.argv", ["klorb"]):
                    cli.main()

    mock_configure_cache.assert_not_called()


def test_main_repl_writes_session_log_by_default() -> None:
    mock_session = MagicMock()
    mock_session.id = "some-session-id"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.run_repl"):
            with patch("klorb.cli.configure_logging") as mock_configure_logging:
                with patch("sys.argv", ["klorb"]):
                    cli.main()

    mock_configure_logging.assert_called_once_with(
        repl_mode=True, log_path=session_log_path("some-session-id"))


def test_main_repl_skips_session_log_when_disabled() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.run_repl"):
            with patch("klorb.cli.configure_logging") as mock_configure_logging:
                with patch("sys.argv", ["klorb", "--no-session-log"]):
                    cli.main()

    mock_configure_logging.assert_called_once_with(repl_mode=True, log_path=None)


def test_main_repl_passes_session_log_enabled_false_when_disabled() -> None:
    mock_session = MagicMock()
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.run_repl") as mock_run_repl:
            with patch("sys.argv", ["klorb", "--no-session-log"]):
                cli.main()

    mock_run_repl.assert_called_once_with(
        mock_session, process_config=mock.ANY, initial_message=None, session_log_enabled=False,
        trust_manager=mock.ANY, config_flag_path=None)


def test_main_one_shot_streams_incrementally_with_single_trailing_newline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    mock_session = MagicMock()

    def fake_run_one_shot(prompt, on_chunk=None):
        on_chunk("Hel")
        on_chunk("lo")
        return "Hello"

    mock_session.run_one_shot.side_effect = fake_run_one_shot
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("sys.argv", ["klorb", "-m", "hi"]):
            cli.main()

    assert capsys.readouterr().out == "Hello\n"


def test_main_dispatches_to_init_subcommand_when_argv1_is_init() -> None:
    with patch("klorb.cli.run_init_cli", return_value=0) as mock_run_init_cli:
        with patch("sys.argv", ["klorb", "init", "--user", "--force"]):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()

    mock_run_init_cli.assert_called_once_with(["--user", "--force"])
    assert exc_info.value.code == 0


def test_main_propagates_init_subcommand_failure_exit_code() -> None:
    with patch("klorb.cli.run_init_cli", return_value=1):
        with patch("sys.argv", ["klorb", "init"]):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()

    assert exc_info.value.code == 1


def test_main_does_not_treat_init_as_a_subcommand_unless_it_is_argv1() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.run_init_cli") as mock_run_init_cli:
            with patch("sys.argv", ["klorb", "-m", "init"]):
                cli.main()

    mock_run_init_cli.assert_not_called()


def test_run_init_cli_defaults_scope_and_prints_messages_to_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.default_scope", return_value="user"):
        with patch("klorb.cli.run_init", return_value=["msg one", "msg two"]) as mock_run_init:
            exit_code = cli.run_init_cli([])

    assert exit_code == 0
    mock_run_init.assert_called_once_with("user", force=False)
    assert capsys.readouterr().err == "msg one\nmsg two\n"


def test_run_init_cli_passes_explicit_scope_and_force() -> None:
    with patch("klorb.cli.run_init", return_value=[]) as mock_run_init:
        cli.run_init_cli(["--system", "--force"])

    mock_run_init.assert_called_once_with("system", force=True)


def test_run_init_cli_rejects_conflicting_scope_flags() -> None:
    with pytest.raises(SystemExit):
        cli.run_init_cli(["--system", "--user"])


def test_run_init_cli_returns_one_and_prints_error_on_init_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.run_init", side_effect=InitError("boom")):
        exit_code = cli.run_init_cli(["--user"])

    assert exit_code == 1
    assert "boom" in capsys.readouterr().err


def test_main_dispatches_to_system_prompt_subcommand_when_argv1_is_system_prompt() -> None:
    with patch("klorb.cli.run_system_prompt_cli", return_value=0) as mock_run:
        with patch("sys.argv", ["klorb", "system-prompt", "--role", "auditor"]):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()

    mock_run.assert_called_once_with(["--role", "auditor"])
    assert exc_info.value.code == 0


def test_main_propagates_system_prompt_subcommand_failure_exit_code() -> None:
    with patch("klorb.cli.run_system_prompt_cli", return_value=1):
        with patch("sys.argv", ["klorb", "system-prompt"]):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()

    assert exc_info.value.code == 1


def test_main_does_not_treat_system_prompt_as_a_subcommand_unless_it_is_argv1() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.run_system_prompt_cli") as mock_run:
            with patch("sys.argv", ["klorb", "-m", "system-prompt"]):
                cli.main()

    mock_run.assert_not_called()


def test_run_system_prompt_cli_defaults_role_to_coordinator(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.load_dotenv"):
        with patch("klorb.cli.TrustManager") as mock_tm_cls:
            mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
                path=Path.cwd(), trusted=False)
            exit_code = cli.run_system_prompt_cli([])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "## System Prompt (default_sys.md)" in out
    assert "## Role-Specific Prompt (role: coordinator)" in out
    assert "## Tool Definitions" in out
    assert "## Token Count Summary" in out
    assert "default_sys.md:" in out
    assert "role-specific prompt:" in out
    assert "tool definitions:" in out
    assert "total" in out


def test_run_system_prompt_cli_passes_explicit_role(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.load_dotenv"):
        with patch("klorb.cli.TrustManager") as mock_tm_cls:
            mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
                path=Path.cwd(), trusted=False)
            exit_code = cli.run_system_prompt_cli(["--role", "auditor"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "## Role-Specific Prompt (role: auditor)" in out


def test_run_system_prompt_cli_passes_explicit_model(
    stub_process_config: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("klorb.cli.load_dotenv"):
        with patch("klorb.cli.TrustManager") as mock_tm_cls:
            mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
                path=Path.cwd(), trusted=False)
            exit_code = cli.run_system_prompt_cli(["--model", "some/model"])

    assert exit_code == 0


def test_run_system_prompt_cli_passes_config_flag_path(
    stub_process_config: MagicMock,
) -> None:
    with patch("klorb.cli.load_dotenv"):
        with patch("klorb.cli.TrustManager") as mock_tm_cls:
            mock_tm_cls.return_value.resolve_workspace.return_value = Workspace(
                path=Path.cwd(), trusted=False)
            cli.run_system_prompt_cli(["--config", "/some/extra-config.json"])

    stub_process_config.assert_called_once_with(
        config_flag_path=Path("/some/extra-config.json"), cwd=mock.ANY, workspace=mock.ANY)


def _make_model(
    name: str,
    *,
    family: str | None = "fam",
    model_version: str | None = "1.0",
    capabilities: dict | None = None,
    settings: dict | None = None,
    klorb_capabilities: dict | None = None,
) -> MagicMock:
    model = MagicMock(spec=Model)
    model.name.return_value = name
    model.family.return_value = family
    model.model_version.return_value = model_version
    model.capabilities.return_value = capabilities if capabilities is not None else {
        "vision": True, "thinking": False, "max_context_window": 1_000, "max_output_tokens": 500,
        "function_calling": True, "streaming": True,
    }
    model.settings.return_value = settings if settings is not None else {}
    model.klorb_capabilities.return_value = klorb_capabilities if klorb_capabilities is not None else {}
    return model


def test_main_dispatches_to_models_subcommand_when_argv1_is_models() -> None:
    with patch("klorb.cli.run_models_cli", return_value=0) as mock_run:
        with patch("sys.argv", ["klorb", "models", "--json"]):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()

    mock_run.assert_called_once_with(["--json"])
    assert exc_info.value.code == 0


def test_main_propagates_models_subcommand_failure_exit_code() -> None:
    with patch("klorb.cli.run_models_cli", return_value=1):
        with patch("sys.argv", ["klorb", "models"]):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()

    assert exc_info.value.code == 1


def test_main_does_not_treat_models_as_a_subcommand_unless_it_is_argv1() -> None:
    mock_session = MagicMock()
    mock_session.run_one_shot.return_value = "reply"
    with patch("klorb.cli.Session", return_value=mock_session):
        with patch("klorb.cli.run_models_cli") as mock_run:
            with patch("sys.argv", ["klorb", "-m", "models"]):
                cli.main()

    mock_run.assert_not_called()


def test_run_models_cli_rejects_json_and_brief_together() -> None:
    with pytest.raises(SystemExit):
        cli.run_models_cli(["--json", "--brief"])


def test_run_models_cli_prints_table_sorted_by_name(capsys: pytest.CaptureFixture[str]) -> None:
    model_b = _make_model("b/model-two")
    model_a = _make_model("a/model-one")
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model_b, model_a]
        exit_code = cli.run_models_cli([])

    assert exit_code == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split()[0] == "NAME"
    assert set(lines[1]) == {"-"}
    assert out.index("a/model-one") < out.index("b/model-two")


def test_run_models_cli_table_has_no_vertical_borders_and_one_rule(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = _make_model("a/model-one")
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        cli.run_models_cli([])

    lines = capsys.readouterr().out.splitlines()
    assert "|" not in "\n".join(lines)
    rule_lines = [line for line in lines if set(line) == {"-"}]
    assert len(rule_lines) == 1
    assert rule_lines[0] == lines[1]


def test_run_models_cli_brief_prints_only_names(capsys: pytest.CaptureFixture[str]) -> None:
    model_b = _make_model("b/model-two")
    model_a = _make_model("a/model-one")
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model_b, model_a]
        exit_code = cli.run_models_cli(["--brief"])

    assert exit_code == 0
    assert capsys.readouterr().out == "a/model-one\nb/model-two\n"


def test_run_models_cli_brief_never_fetches_costs(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model("a/model-one")
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        with patch("klorb.cli.fetch_openrouter_pricing_for_models") as mock_fetch:
            cli.run_models_cli(["--brief", "--costs"])

    mock_fetch.assert_not_called()


def test_run_models_cli_json_emits_array_of_model_dicts(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model(
        "a/model-one", family="fam", model_version="1.0",
        capabilities={"vision": True}, settings={"temperature": 0.1},
        klorb_capabilities={"FOO": True})
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        exit_code = cli.run_models_cli(["--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [{
        "name": "a/model-one",
        "family": "fam",
        "model_version": "1.0",
        "settings": {"temperature": 0.1},
        "capabilities": {"vision": True},
        "klorb_capabilities": {"FOO": True},
    }]


def test_run_models_cli_json_has_no_costs_key_without_flag(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model("a/model-one")
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        cli.run_models_cli(["--json"])

    payload = json.loads(capsys.readouterr().out)
    assert "costs" not in payload[0]


def test_run_models_cli_json_costs_includes_pricing(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model("a/model-one")
    pricing = ModelPricing(input_cost_per_mtok=1.5, output_cost_per_mtok=3.0)
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        with patch("klorb.cli.fetch_openrouter_pricing_for_models", return_value={"a/model-one": pricing}):
            exit_code = cli.run_models_cli(["--json", "--costs"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["costs"] == {
        "input_cost_per_mtok": 1.5,
        "output_cost_per_mtok": 3.0,
        "currency": "USD",
    }


def test_run_models_cli_json_costs_null_when_pricing_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    model = _make_model("a/model-one")
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        with patch("klorb.cli.fetch_openrouter_pricing_for_models", return_value={"a/model-one": None}):
            cli.run_models_cli(["--json", "--costs"])

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["costs"] is None


def test_run_models_cli_costs_adds_table_columns(capsys: pytest.CaptureFixture[str]) -> None:
    model = _make_model("a/model-one")
    pricing = ModelPricing(input_cost_per_mtok=1.5, output_cost_per_mtok=3.0)
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model]
        with patch("klorb.cli.fetch_openrouter_pricing_for_models", return_value={"a/model-one": pricing}):
            cli.run_models_cli(["--costs"])

    out = capsys.readouterr().out
    assert "IN $/MTOK" in out
    assert "OUT $/MTOK" in out
    assert "1.5" in out
    assert "3" in out


def test_run_models_cli_costs_passes_all_model_names(capsys: pytest.CaptureFixture[str]) -> None:
    model_a = _make_model("a/model-one")
    model_b = _make_model("b/model-two")
    with patch("klorb.cli.ModelRegistry") as mock_registry_cls:
        mock_registry_cls.return_value.models.return_value = [model_b, model_a]
        with patch("klorb.cli.fetch_openrouter_pricing_for_models", return_value={}) as mock_fetch:
            cli.run_models_cli(["--costs"])

    mock_fetch.assert_called_once_with(["a/model-one", "b/model-two"])
