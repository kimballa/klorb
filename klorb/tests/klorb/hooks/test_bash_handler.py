# © Copyright 2026 Aaron Kimball
"""Tests for klorb.hooks.bash_handler.run_bash_handler: the `type=bash` hook handler."""

from pathlib import Path
from typing import Any

import pytest

from klorb import sandbox
from klorb.config_macros import MacroExpansionError
from klorb.hooks.bash_handler import HOOK_ENV_FILE_VAR, _handler_argv, run_bash_handler
from klorb.hooks.config import HookConfig
from klorb.hooks.wire import HookInput
from klorb.permissions.directory_access import DirRules
from klorb.session.config import SessionConfig
from klorb.workspace import Workspace

requires_bwrap = pytest.mark.skipif(
    not sandbox.bwrap_available(),
    reason="bwrap cannot create a sandbox here (missing binary or no unprivileged user namespaces)")


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test unsandboxed unless it opts in, mirroring `klorb.tools.test_bash`'s own
    fixture -- deterministic regardless of whether the host running the suite has a working
    `bwrap`."""
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


def _session_config(workspace_root: Path, **overrides: Any) -> SessionConfig:
    defaults: dict[str, Any] = {
        "workspace": Workspace(path=workspace_root, trusted=True),
        "read_dirs": DirRules(allow=[workspace_root]),
        "write_dirs": DirRules(allow=[workspace_root]),
    }
    defaults.update(overrides)
    return SessionConfig(**defaults)


def _hook_input(workspace_root: Path, **overrides: Any) -> HookInput:
    defaults: dict[str, Any] = {
        "hook": "onProcessStart", "event": "Startup", "workspaceRoot": str(workspace_root),
    }
    defaults.update(overrides)
    return HookInput(**defaults)


# --- argv construction ---


def test_handler_argv_uses_bash_command_for_shell() -> None:
    handler = HookConfig(type="bash", shell="echo hi")
    assert _handler_argv(handler, "/bin/bash", Path("/ws")) == ["/bin/bash", "-c", "echo hi"]


def test_handler_argv_does_not_expand_macros_in_shell() -> None:
    handler = HookConfig(type="bash", shell="echo ${workspaceRoot}")
    argv = _handler_argv(handler, "/bin/bash", Path("/ws"))
    assert argv == ["/bin/bash", "-c", "echo ${workspaceRoot}"]


def test_handler_argv_expands_macros_in_command(tmp_path: Path) -> None:
    handler = HookConfig(type="bash", command=["mytool", "${workspaceRoot}/sub", "${home}"])
    argv = _handler_argv(handler, "/bin/bash", tmp_path)
    resolved_root = str(tmp_path.resolve(strict=False))
    assert argv == ["mytool", f"{resolved_root}/sub", str(Path.home())]


def test_handler_argv_raises_for_unrecognized_macro(tmp_path: Path) -> None:
    handler = HookConfig(type="bash", command=["mytool", "${bogus}"])
    with pytest.raises(MacroExpansionError):
        _handler_argv(handler, "/bin/bash", tmp_path)


def test_handler_argv_is_none_with_neither_shell_nor_command() -> None:
    handler = HookConfig(type="bash")
    assert _handler_argv(handler, "/bin/bash", Path("/ws")) is None


# --- run_bash_handler: success path ---


def test_shell_handler_returns_parsed_hook_output(tmp_path: Path) -> None:
    handler = HookConfig(type="bash", shell='echo \'{"message": "hi"}\'')
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=5.0)
    assert result is not None
    assert result.success is True
    assert result.message == "hi"


def test_handler_receives_hook_input_json_on_stdin(tmp_path: Path) -> None:
    handler = HookConfig(
        type="bash",
        shell=(
            'python3 -c \'import sys, json; data = json.load(sys.stdin); '
            'print(json.dumps({"message": data["hook"] + ":" + data["name"]}))\''))
    result = run_bash_handler(
        handler, _hook_input(tmp_path, name="my-handler"), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=5.0)
    assert result is not None
    assert result.message == "onProcessStart:my-handler"


def test_command_handler_runs_with_expanded_argv(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    handler = HookConfig(
        type="bash",
        command=["/bin/bash", "-c", f'echo -n "" > "{marker}"; echo \'{{"message": "done"}}\''])
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=5.0)
    assert result is not None
    assert result.message == "done"
    assert marker.exists()


def test_hook_env_file_var_points_to_a_writable_file(tmp_path: Path) -> None:
    handler = HookConfig(
        type="bash",
        shell=f'test -w "${HOOK_ENV_FILE_VAR}" && echo \'{{"message": "writable"}}\'')
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=5.0)
    assert result is not None
    assert result.message == "writable"


def test_hook_env_file_is_removed_after_the_handler_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_files_dir = tmp_path / "hook-env-files"
    monkeypatch.setattr("klorb.hooks.bash_handler._HOOK_ENV_FILES_DIR", env_files_dir)
    handler = HookConfig(type="bash", shell="echo '{}'")
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=5.0)
    assert result is not None
    assert list(env_files_dir.iterdir()) == []


# --- run_bash_handler: error handling (never raises, returns None) ---


def test_neither_shell_nor_command_returns_none(tmp_path: Path) -> None:
    handler = HookConfig(type="bash")
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=5.0)
    assert result is None


def test_malformed_macro_returns_none(tmp_path: Path) -> None:
    handler = HookConfig(type="bash", command=["mytool", "${bogus}"])
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=5.0)
    assert result is None


def test_nonzero_exit_returns_none(tmp_path: Path) -> None:
    handler = HookConfig(type="bash", shell="exit 1")
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=5.0)
    assert result is None


def test_timeout_returns_none(tmp_path: Path) -> None:
    handler = HookConfig(type="bash", shell="sleep 5")
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=0.2)
    assert result is None


def test_invalid_json_output_returns_none(tmp_path: Path) -> None:
    handler = HookConfig(type="bash", shell="echo 'not json'")
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=5.0)
    assert result is None


# --- sandboxed execution ---


@requires_bwrap
def test_sandboxed_handler_can_read_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", sandbox.bwrap_available)
    (tmp_path / "marker.txt").write_text("hi")
    handler = HookConfig(
        type="bash",
        shell='cat "$WORKSPACE_ROOT/marker.txt" > /dev/null && echo \'{"message": "read-ok"}\'')
    result = run_bash_handler(
        handler, _hook_input(tmp_path), session_config=_session_config(tmp_path),
        bash_command="/bin/bash", timeout_seconds=10.0)
    assert result is not None
    assert result.message == "read-ok"
