# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.bash.BashTool: permission classification (CommandRules + readDirs/
writeDirs on redirection targets), execution (env building, timeout, signal decoding, stdout/
stderr capture and spill), and the bash -i noise-stripping. See
docs/plans/ready/004-bash-permissions-and-bash-tool.md.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from klorb.permissions.command_access import CommandRules
from klorb.permissions.directory_access import DirRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools import bash as bash_module
from klorb.tools.bash import BashTool, build_bash_env
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    workspace_root: Path,
    *,
    command_rules: CommandRules | None = None,
    read_dirs: DirRules | None = None,
    write_dirs: DirRules | None = None,
    process_config: ProcessConfig | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=process_config or ProcessConfig(),
        session_config=SessionConfig(
            workspace=Workspace(path=workspace_root, trusted=True),
            read_dirs=read_dirs or DirRules(allow=[workspace_root]),
            write_dirs=write_dirs or DirRules(allow=[workspace_root]),
            command_rules=command_rules or CommandRules()))


@pytest.fixture(autouse=True)
def _reset_sandbox_warnings() -> Iterator[None]:
    bash_module._warned_sessions.clear()
    yield
    bash_module._warned_sessions.clear()


# --- permission classification ---


def test_allowed_command_runs(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    result = tool.apply({"command": "echo hello"})
    assert result["success"] is True
    assert result["stdout"] == "hello\n"
    assert result["exit_status"] == 0
    assert result["failure_reason"] is None


def test_command_with_no_matching_rule_asks(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules())
    tool = BashTool(context)
    with pytest.raises(PermissionAskRequired):
        tool.apply({"command": "echo hello"})


def test_denied_command_denies(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(deny=[["rm", "*"]]))
    tool = BashTool(context)
    with pytest.raises(PermissionError):
        tool.apply({"command": "rm -rf /tmp/whatever"})


def test_non_literal_argument_asks_even_with_broad_allow(tmp_path: Path) -> None:
    """A blanket allow-rule for echo must not let a variable-expansion argument slip through --
    the walker's own fail-closed 'ask' for non-literal tokens is a structural override."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    with pytest.raises(PermissionAskRequired):
        tool.apply({"command": "echo $HOME"})


def test_write_redirect_checked_against_write_dirs(tmp_path: Path) -> None:
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]),
        write_dirs=DirRules())  # nothing explicitly allowed -> write normalizes to "ask"
    tool = BashTool(context)
    with pytest.raises(PermissionAskRequired):
        tool.apply({"command": f"echo hi > {tmp_path / 'out.txt'}"})


def test_write_redirect_allowed_when_write_dirs_allows(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    out_file = tmp_path / "out.txt"
    result = tool.apply({"command": f"echo hi > {out_file}"})
    assert result["success"] is True
    assert out_file.read_text() == "hi\n"


def test_write_redirect_outside_workspace_raises(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    context = _context(workspace, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    with pytest.raises(PermissionError):
        tool.apply({"command": f"echo hi > {outside}"})


def test_empty_command_raises_value_error(tmp_path: Path) -> None:
    context = _context(tmp_path)
    tool = BashTool(context)
    with pytest.raises(ValueError, match="empty"):
        tool.apply({"command": "   "})


# --- execution ---


def test_nonzero_exit_reports_failure(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["false"]]))
    tool = BashTool(context)
    result = tool.apply({"command": "false"})
    assert result["success"] is False
    assert result["exit_status"] == 1
    assert result["failure_reason"] == "Process completed normally with non-zero status"


def test_signal_death_is_decoded(tmp_path: Path) -> None:
    """A simple external command in tail position gets exec()'d directly by the outer bash
    (tail-call optimization, verified empirically -- see BashTool._decode_exit's docstring), so
    Python observes a *negative* returncode here, not a positive 128+signum one; both shapes
    must decode to the same human-readable signal name."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["python3", "*"]]))
    tool = BashTool(context)
    result = tool.apply({
        "command": "python3 -c \"import os, signal; os.kill(os.getpid(), signal.SIGSEGV)\"",
    })
    assert result["success"] is False
    assert result["exit_status"] == -11
    assert "SEGV" in result["failure_reason"]


def test_timeout_kills_process_and_reports_it(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    process_config.bash_timeout_seconds = 0.3
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["sleep", "*"]]), process_config=process_config)
    tool = BashTool(context)
    result = tool.apply({"command": "sleep 5"})
    assert result["success"] is False
    assert "timed out" in result["failure_reason"]
    assert result["runtime"] < 2.0


def test_stderr_is_captured(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["bash", "*"]]))
    tool = BashTool(context)
    result = tool.apply({"command": "bash -c 'echo oops 1>&2'"})
    assert result["stderr"] == "oops\n"


def test_bash_startup_noise_is_stripped_from_stderr(tmp_path: Path) -> None:
    """bash -i always writes two fixed lines to stderr with no controlling tty; they must never
    reach the model."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["true"]]))
    tool = BashTool(context)
    result = tool.apply({"command": "true"})
    assert result["stderr"] == ""


def test_output_over_spill_bytes_is_written_to_a_file(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    process_config.bash_spill_bytes = 10
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]), process_config=process_config)
    tool = BashTool(context)
    result = tool.apply({"command": "echo this-is-longer-than-ten-bytes"})
    assert result["stdout"] is None
    assert result["stdout_file"] is not None
    assert Path(result["stdout_file"]).read_text().strip() == "this-is-longer-than-ten-bytes"


def test_spilled_output_dir_gets_an_automatic_read_grant(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    process_config.bash_spill_bytes = 1
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]), process_config=process_config)
    tool = BashTool(context)
    result = tool.apply({"command": "echo hi"})
    spilled_dir = Path(result["stdout_file"]).parent
    assert spilled_dir in context.session_config.read_dirs.allow


def test_sandbox_notice_appears_once_per_session(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["true"]]))
    tool1 = BashTool(context)
    result1 = tool1.apply({"command": "true"})
    assert "sandbox_notice" in result1

    tool2 = BashTool(context)
    result2 = tool2.apply({"command": "true"})
    assert "sandbox_notice" not in result2


# --- build_bash_env ---


def test_build_bash_env_always_shares_home_and_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/someone")
    monkeypatch.setenv("USER", "someone")
    monkeypatch.delenv("UNRELATED_SECRET", raising=False)
    env = build_bash_env(SessionConfig())
    assert env == {"HOME": "/home/someone", "USER": "someone"}


def test_build_bash_env_shares_configured_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOOLCHAIN_VAR", "abc")
    env = build_bash_env(SessionConfig(share_env=["MY_TOOLCHAIN_VAR"]))
    assert env.get("MY_TOOLCHAIN_VAR") == "abc"


def test_build_bash_env_set_env_overrides_shared_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOO", "from-process")
    env = build_bash_env(SessionConfig(share_env=["FOO"], set_env={"FOO": "overridden"}))
    assert env["FOO"] == "overridden"


def test_build_bash_env_does_not_share_unlisted_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_OTHER_VAR", "value")
    env = build_bash_env(SessionConfig())
    assert "SOME_OTHER_VAR" not in env


def test_share_env_variable_is_visible_to_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-quoted inner command so $KLORB_TEST_VAR is only expanded once bash actually runs
    it (with the env we built), not by shfmt/the outer shell's own parse-time interpolation."""
    monkeypatch.setenv("KLORB_TEST_VAR", "visible-value")
    context = _context(tmp_path, command_rules=CommandRules(allow=[["bash", "*"]]))
    context.session_config.share_env = ["KLORB_TEST_VAR"]
    tool = BashTool(context)
    result = tool.apply({"command": "bash -c 'echo $KLORB_TEST_VAR'"})
    assert result["stdout"] == "visible-value\n"


def test_unshared_variable_is_not_visible_to_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KLORB_TEST_SECRET", "should-not-leak")
    context = _context(tmp_path, command_rules=CommandRules(allow=[["bash", "*"]]))
    tool = BashTool(context)
    result = tool.apply({"command": "bash -c 'echo $KLORB_TEST_SECRET'"})
    assert result["stdout"] == "\n"
