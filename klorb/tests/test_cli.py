# © Copyright 2026 Aaron Kimball
"""Tests for klorb.cli."""

from collections.abc import Iterator
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from klorb import cli
from klorb.klorb_init import InitError
from klorb.logging_config import session_log_path
from klorb.openrouter import DEFAULT_MODEL
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.session import ThinkingEffort
from klorb.workspace import trust_manager as trust_manager_module


@pytest.fixture(autouse=True)
def _isolate_projects_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point `klorb.workspace.trust_manager.projects_path()` (and so the real `TrustManager()`
    `cli.main()` constructs) at an empty location under `tmp_path`, so no test in this module
    reads or writes the developer's own `$KLORB_DATA_DIR/projects.json`."""
    monkeypatch.setattr(trust_manager_module, "KLORB_DATA_DIR", tmp_path / "data")


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
