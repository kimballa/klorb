# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.bash.BashTool: permission classification (CommandRules + readDirs/
writeDirs on redirection targets), execution (env building, timeout, signal decoding, stdout/
stderr capture and spill), and the bash -i noise-stripping. See
docs/specs/bash-tool-and-command-permissions.md.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from klorb import sandbox
from klorb.api_provider import ProviderResponse
from klorb.message import Message, ToolCallRequest
from klorb.permissions.command_access import CommandRules
from klorb.permissions.directory_access import DirRules
from klorb.permissions.file_access import FileRules
from klorb.permissions.resource import BashCommandContext, StructuralResource
from klorb.permissions.table import MultiPermissionAskRequired, PermissionAskItem
from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.bash import BashTool, build_bash_env
from klorb.tools.registry import ToolRegistry
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace

_live_sessions: list[Session] = []


def _bash_context(item: PermissionAskItem) -> BashCommandContext:
    assert item.bash_context is not None
    return item.bash_context


requires_bwrap = pytest.mark.skipif(
    not sandbox.bwrap_available(),
    reason="bwrap cannot create a sandbox here (missing binary or no unprivileged user namespaces)")
"""Skip (never xfail/error) the opt-in tests that exercise a real `bwrap` sandbox — shared with
the runtime's own `bwrap_available()` gate, per the plan's 'klorb's own test suite' note."""


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every `BashTool` test unsandboxed unless it opts in, so the plumbing tests below
    (env building, signal decoding, timeout, persistent-shell process identity) are deterministic
    regardless of whether the host running the suite happens to have a working `bwrap` — matching
    how a container/cloud-agent CI environment (no unprivileged user namespaces) already runs them.
    A test that specifically covers sandboxed behavior re-patches `bwrap_available` back to the
    real `klorb.sandbox.bwrap_available` in its own body (which wins, running after this fixture)
    and gates itself on `requires_bwrap`."""
    monkeypatch.setattr("klorb.tools.bash.bwrap_available", lambda: False)


@pytest.fixture(autouse=True)
def _close_sessions_created_by_context() -> Any:
    """Close every `Session` `_context()` hands out, once the test using it finishes.

    Several `shell_lifetime="session"`/`"new"` tests below spawn a real, live `PersistentShell`
    (a `bash` subprocess plus two background reader threads -- see `klorb.tools.bash.
    PersistentShell`) and never call `Session.close()` themselves. Left alone, that subprocess
    and its threads outlive the test that started them for the rest of the pytest process (see
    `Session.close`'s docstring): harmless in isolation, but they accumulate release-over-
    release as more such tests run in the same process, alongside whatever unrelated tests ran
    before or after them.
    """
    _live_sessions.clear()
    yield
    for session in _live_sessions:
        session.close()
    _live_sessions.clear()


def _context(
    workspace_root: Path,
    *,
    command_rules: CommandRules | None = None,
    read_dirs: DirRules | None = None,
    write_dirs: DirRules | None = None,
    read_files: FileRules | None = None,
    write_files: FileRules | None = None,
    process_config: ProcessConfig | None = None,
    with_session: bool = True,
) -> ToolSetupContext:
    session_config = SessionConfig(
        workspace=Workspace(path=workspace_root, trusted=True),
        read_dirs=read_dirs or DirRules(allow=[workspace_root]),
        write_dirs=write_dirs or DirRules(allow=[workspace_root]),
        read_files=read_files or FileRules(), write_files=write_files or FileRules(),
        command_rules=command_rules or CommandRules())
    session = Session(config=session_config) if with_session else None
    if session is not None:
        _live_sessions.append(session)
    return ToolSetupContext(
        process_config=process_config or ProcessConfig(), session_config=session_config, session=session)


def _apply(tool: BashTool, command: str, **extra: Any) -> Any:
    extra.setdefault("intent", "test intent")
    return tool.apply({"command": command, "shell_lifetime": "command", **extra})


# --- permission classification ---


def test_allowed_command_runs(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    result = _apply(tool, "echo hello")
    assert result["success"] is True
    assert result["stdout"] == "hello\n"
    assert result["exit_status"] == 0
    assert result["failure_reason"] is None


def test_command_with_no_matching_rule_asks(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules())
    tool = BashTool(context)
    with pytest.raises(MultiPermissionAskRequired):
        _apply(tool, "echo hello")


def test_denied_command_denies(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(deny=[["rm", "**"]]))
    tool = BashTool(context)
    with pytest.raises(PermissionError):
        _apply(tool, "rm -rf /tmp/whatever")


def test_non_literal_argument_asks_even_with_broad_allow(tmp_path: Path) -> None:
    """A blanket allow-rule for echo must not let a variable-expansion argument slip through --
    the walker's own fail-closed 'ask' for non-literal tokens is a structural override."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    with pytest.raises(MultiPermissionAskRequired):
        _apply(tool, "echo $HOME")


def test_non_literal_argument_ask_item_names_the_actual_command(tmp_path: Path) -> None:
    """A structural (forced-ask-reason) item's command_text must show what command it's actually
    about, not just an abstract resource_description reason with no command in sight -- see
    docs/adrs/structural-ask-items-include-the-raw-command-text.md."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    with pytest.raises(MultiPermissionAskRequired) as exc_info:
        _apply(tool, "echo $HOME")

    structural_items = [
        item for item in exc_info.value.items if isinstance(item.resource, StructuralResource)]
    assert structural_items
    assert all(_bash_context(item).command_text == "echo $HOME" for item in structural_items)
    assert any("non-literal argument" in item.resource_description for item in structural_items)


def test_single_command_ask_item_is_not_compound(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules())
    tool = BashTool(context)
    with pytest.raises(MultiPermissionAskRequired) as exc_info:
        _apply(tool, "echo hello")

    assert all(_bash_context(item).is_compound is False for item in exc_info.value.items)


def test_compound_command_ask_items_are_marked_compound(tmp_path: Path) -> None:
    """`foo && bar` needs a decision for each simple command, and every one of those items must
    say so via `is_compound` -- see
    docs/adrs/always-show-more-indicator-for-compound-command-ask-items.md."""
    context = _context(tmp_path, command_rules=CommandRules())
    tool = BashTool(context)
    with pytest.raises(MultiPermissionAskRequired) as exc_info:
        _apply(tool, "echo hello && echo goodbye")

    assert len(exc_info.value.items) == 2
    assert all(_bash_context(item).is_compound is True for item in exc_info.value.items)
    assert all(
        _bash_context(item).command_text == "echo hello && echo goodbye"
        for item in exc_info.value.items)


def test_for_loop_body_with_non_literal_arguments_is_marked_compound(tmp_path: Path) -> None:
    """Regression test: a `for` loop body of two non-literal-argument commands (e.g. referencing
    the loop variable) previously computed is_compound=False, since neither command reaches
    analysis.simple_commands -- see docs/adrs/
    always-show-more-indicator-for-compound-command-ask-items.md."""
    context = _context(tmp_path, command_rules=CommandRules())
    tool = BashTool(context)
    with pytest.raises(MultiPermissionAskRequired) as exc_info:
        _apply(tool, 'for f in *.txt; do echo "$f"; rm "$f"; done')

    assert len(exc_info.value.items) == 2
    assert all(_bash_context(item).is_compound is True for item in exc_info.value.items)


def test_compound_command_ask_items_each_carry_their_own_item_command_text(tmp_path: Path) -> None:
    """Regression test: `echo $SHELL; echo $HOME` previously gave every ask item the same
    generic resource_description and the same full command_text, with no way for the user to
    tell which item is about which command -- item_command_text now names each item's own
    statement -- see docs/adrs/
    permission-ask-item-shows-its-own-command-text-not-the-full-compound.md."""
    context = _context(tmp_path, command_rules=CommandRules())
    tool = BashTool(context)
    with pytest.raises(MultiPermissionAskRequired) as exc_info:
        _apply(tool, "echo $SHELL; echo $HOME")

    assert len(exc_info.value.items) == 2
    assert [_bash_context(item).item_command_text for item in exc_info.value.items] == [
        "echo $SHELL", "echo $HOME"]
    assert all(
        _bash_context(item).command_text == "echo $SHELL; echo $HOME"
        for item in exc_info.value.items)


def test_ask_items_all_carry_the_calls_intent(tmp_path: Path) -> None:
    """`intent` is the model's own required argument -- every ask item a single `BashTool` call
    produces (one per simple command/redirect/structural reason) should carry the same one, the
    same way they all share `command_text`."""
    context = _context(tmp_path, command_rules=CommandRules())
    tool = BashTool(context)
    with pytest.raises(MultiPermissionAskRequired) as exc_info:
        _apply(tool, "echo hello && echo goodbye", intent="Greet twice")

    assert len(exc_info.value.items) == 2
    assert all(_bash_context(item).intent == "Greet twice" for item in exc_info.value.items)


def test_redirect_ask_item_command_text_is_the_whole_statement(tmp_path: Path) -> None:
    """A redirect item's item_command_text should show the whole command line the redirect
    belongs to (e.g. "echo hi > out.txt"), not just the bare target path -- so distinct redirect
    items from a compound command are also individually distinguishable."""
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]), write_dirs=DirRules())
    tool = BashTool(context)
    out_file = tmp_path / "out.txt"
    with pytest.raises(MultiPermissionAskRequired) as exc_info:
        _apply(tool, f"echo hi > {out_file}")

    assert len(exc_info.value.items) == 1
    assert _bash_context(exc_info.value.items[0]).item_command_text == f"echo hi > {out_file}"


def test_write_redirect_checked_against_write_dirs(tmp_path: Path) -> None:
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]),
        write_dirs=DirRules())  # nothing explicitly allowed -> write normalizes to "ask"
    tool = BashTool(context)
    with pytest.raises(MultiPermissionAskRequired):
        _apply(tool, f"echo hi > {tmp_path / 'out.txt'}")


def test_write_redirect_allowed_when_write_dirs_allows(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    out_file = tmp_path / "out.txt"
    result = _apply(tool, f"echo hi > {out_file}")
    assert result["success"] is True
    assert out_file.read_text() == "hi\n"


def test_write_redirect_outside_workspace_raises(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    context = _context(workspace, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    with pytest.raises(PermissionError):
        _apply(tool, f"echo hi > {outside}")


def test_write_redirect_to_device_file_allowed_by_writefiles_bypasses_workspace_boundary(
    tmp_path: Path,
) -> None:
    """The motivating case for readFiles/writeFiles: a redirect to a path outside the
    workspace root (like a character device) succeeds when writeFiles.allow names it exactly,
    even though the same target would otherwise hit the hard workspace-root boundary raise --
    see test_write_redirect_outside_workspace_raises above for that boundary without the
    writeFiles carve-out."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    device = tmp_path / "outside-device"
    context = _context(
        workspace, command_rules=CommandRules(allow=[["echo", "*"]]),
        write_files=FileRules(allow=[device]))
    tool = BashTool(context)
    result = _apply(tool, f"echo hi > {device}")
    assert result["success"] is True
    assert device.read_text() == "hi\n"


def test_empty_command_raises_value_error(tmp_path: Path) -> None:
    context = _context(tmp_path)
    tool = BashTool(context)
    with pytest.raises(ValueError, match="empty"):
        _apply(tool, "   ")


def test_unknown_shell_lifetime_raises_value_error(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    with pytest.raises(ValueError, match="shell_lifetime"):
        tool.apply({"command": "echo hi", "intent": "test intent", "shell_lifetime": "bogus"})


@pytest.mark.parametrize("args_extra", [{}, {"shell_lifetime": None}, {"shell_lifetime": ""}])
def test_omitted_shell_lifetime_runs_command_scoped(
    tmp_path: Path, args_extra: dict[str, Any],
) -> None:
    # A missing key, a null, or an empty string all mean a fresh, command-scoped shell -- so the
    # command runs successfully and the result carries none of the persistent-shell-only fields.
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    result = tool.apply({"command": "echo hi", "intent": "test intent", **args_extra})
    assert result["success"] is True
    assert result["stdout"].strip() == "hi"
    assert "terminal_alive" not in result


# --- execution ---


def test_nonzero_exit_reports_failure(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["false"]]))
    tool = BashTool(context)
    result = _apply(tool, "false")
    assert result["success"] is False
    assert result["exit_status"] == 1
    assert result["failure_reason"] == "Process completed normally with non-zero status"


def test_is_success_reflects_a_nonzero_exit(tmp_path: Path) -> None:
    """A command that ran but exited non-zero doesn't raise, so `error` is None; `is_success`
    must consult the result's own `"success"` flag (used for per-tool statistics) rather than
    the base class's `error is None` heuristic, which would miscount the failure as a success."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["false"], ["true"]]))
    tool = BashTool(context)
    failed = _apply(tool, "false")
    assert failed["success"] is False
    assert tool.is_success(args={}, result=failed, error=None) is False
    ok = _apply(tool, "true")
    assert ok["success"] is True
    assert tool.is_success(args={}, result=ok, error=None) is True
    assert tool.is_success(args={}, result=None, error="boom") is False


def test_session_dispatch_reports_nonzero_exit_as_business_logic_error(tmp_path: Path) -> None:
    """`BashTool.apply()` never raises for a command that merely runs and fails (see
    `test_is_success_reflects_a_nonzero_exit` above), so `Session._run_tool_calls` must derive
    the `ToolResponseEnvelope`'s `business_logic` categorization from `is_success()` itself, one
    layer up -- see docs/plans/archive/015-structured-tool-responses.md's "Design decision: Bash
    exit-status handling". `response_body` still carries the tool's own result (stdout/stderr/
    exit_status), even though `is_error` is `True`, so a failed command's diagnostic detail isn't
    dropped."""
    config = SessionConfig(
        model="some/model", workspace=Workspace(path=tmp_path, trusted=True),
        command_rules=CommandRules(allow=[["false"]]))
    tool_registry = ToolRegistry.discover_tools(ProcessConfig(), config)
    mock_provider = MagicMock()
    mock_provider.send_prompt.side_effect = [
        ProviderResponse(
            message=Message(
                content="", role="assistant", num_tokens=3, processing_state="complete",
                timestamp=datetime.now(), finish_reason="tool_calls",
                tool_calls=[ToolCallRequest(
                    id="call_1", name="Bash",
                    arguments=json.dumps({
                        "command": "false", "shell_lifetime": "command", "intent": "test intent"}))]),
            prompt_tokens=10),
        ProviderResponse(
            message=Message(
                content="done", role="assistant", num_tokens=1, processing_state="complete",
                timestamp=datetime.now(), finish_reason="stop"),
            prompt_tokens=10),
    ]
    session = Session(config, provider=mock_provider, tool_registry=tool_registry)
    _live_sessions.append(session)

    response = session.send_turn("run false")

    assert response == "done"
    tool_response = next(m for m in session.messages if m.role == "tool_response")
    assert isinstance(tool_response.content, str)
    envelope = json.loads(tool_response.content)
    assert envelope["is_error"] is True
    assert envelope["is_retryable"] is False
    assert envelope["error_category"] == "business_logic"
    assert "error_message" not in envelope
    assert envelope["response_body"]["success"] is False
    assert envelope["response_body"]["exit_status"] == 1


def test_signal_death_is_decoded(tmp_path: Path) -> None:
    """A simple external command in tail position gets exec()'d directly by the outer bash
    (tail-call optimization, verified empirically -- see BashTool._decode_exit's docstring), so
    Python observes a *negative* returncode here, not a positive 128+signum one; both shapes
    must decode to the same human-readable signal name."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["python3", "**"]]))
    tool = BashTool(context)
    result = _apply(
        tool, "python3 -c \"import os, signal; os.kill(os.getpid(), signal.SIGSEGV)\"")
    assert result["success"] is False
    assert result["exit_status"] == -11
    assert "SEGV" in result["failure_reason"]


def test_timeout_kills_process_and_reports_it(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    process_config.bash_timeout_seconds = 0.3
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["sleep", "*"]]), process_config=process_config)
    tool = BashTool(context)
    result = _apply(tool, "sleep 5")
    assert result["success"] is False
    assert "timed out" in result["failure_reason"]
    assert result["runtime"] < 2.0


def test_cancel_event_sends_sigint_and_reports_cancellation(tmp_path: Path) -> None:
    """Setting `Session.active_cancel_event` while a one-shot (`shell_lifetime="command"`)
    command is running sends `SIGINT` to it immediately rather than waiting for
    `tools.bash.timeout` to elapse — see `BashTool._execute`."""
    process_config = ProcessConfig()
    process_config.bash_timeout_seconds = 30.0
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["sleep", "*"]]), process_config=process_config)
    assert context.session is not None
    context.session.active_cancel_event = threading.Event()
    tool = BashTool(context)

    def _cancel_soon() -> None:
        time.sleep(0.3)
        assert context.session is not None
        assert context.session.active_cancel_event is not None
        context.session.active_cancel_event.set()

    threading.Thread(target=_cancel_soon, daemon=True).start()

    start = time.monotonic()
    result = _apply(tool, "sleep 30")
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert result["success"] is False
    assert result["exit_status"] != 0
    assert "canceled" in result["failure_reason"].lower()
    assert "SIGINT Triggered by user" in result["stderr"]


def test_stderr_is_captured(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["bash", "**"]]))
    tool = BashTool(context)
    result = _apply(tool, "bash -c 'echo oops 1>&2'")
    assert result["stderr"] == "oops\n"


def test_bash_startup_noise_is_stripped_from_stderr(tmp_path: Path) -> None:
    """bash -i always writes two fixed lines to stderr with no controlling tty; they must never
    reach the model."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["true"]]))
    tool = BashTool(context)
    result = _apply(tool, "true")
    assert result["stderr"] == ""


def test_output_over_spill_bytes_is_written_to_a_file(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    process_config.bash_spill_bytes = 10
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]), process_config=process_config)
    tool = BashTool(context)
    result = _apply(tool, "echo this-is-longer-than-ten-bytes")
    assert result["stdout"] is None
    assert result["stdout_file"] is not None
    assert Path(result["stdout_file"]).read_text().strip() == "this-is-longer-than-ten-bytes"


def test_spilled_output_dir_gets_an_automatic_read_grant(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    process_config.bash_spill_bytes = 1
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]), process_config=process_config)
    tool = BashTool(context)
    result = _apply(tool, "echo hi")
    spilled_dir = Path(result["stdout_file"]).parent
    assert spilled_dir in context.session_config.read_dirs.allow


def test_sandbox_notice_appears_once_per_session(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["true"]]))
    tool1 = BashTool(context)
    result1 = _apply(tool1, "true")
    assert "sandbox_notice" in result1

    tool2 = BashTool(context)
    result2 = _apply(tool2, "true")
    assert "sandbox_notice" not in result2


def test_sandbox_notice_without_a_session_degrades_gracefully(tmp_path: Path) -> None:
    """A ToolSetupContext built with no Session (e.g. a caller that constructs one directly,
    like most other tools' tests) has nowhere to record "already warned" -- BashTool must still
    work, just without the per-session dedup."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["true"]]), with_session=False)
    assert context.session is None
    tool = BashTool(context)
    result = _apply(tool, "true")
    assert result["success"] is True
    assert "sandbox_notice" in result


# --- build_bash_env ---

_BASH_COMMAND = "/opt/klorb-test/bash"
"""A distinctive path (never a real default) so tests asserting `SHELL`/`BASH` pass through
`build_bash_env`'s `bash_command` argument can't coincidentally match some other default."""


def test_build_bash_env_always_shares_home_and_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/home/someone")
    monkeypatch.setenv("USER", "someone")
    monkeypatch.delenv("UNRELATED_SECRET", raising=False)
    env = build_bash_env(SessionConfig(workspace=Workspace(path=tmp_path)), _BASH_COMMAND)
    assert env == {
        "HOME": "/home/someone", "USER": "someone", "WORKSPACE_ROOT": str(tmp_path.resolve()),
        "SHELL": _BASH_COMMAND, "BASH": _BASH_COMMAND}


def test_build_bash_env_always_sets_workspace_root(tmp_path: Path) -> None:
    env = build_bash_env(SessionConfig(workspace=Workspace(path=tmp_path)), _BASH_COMMAND)
    assert env["WORKSPACE_ROOT"] == str(tmp_path.resolve())


def test_build_bash_env_always_sets_shell_and_bash_to_bash_command(tmp_path: Path) -> None:
    env = build_bash_env(SessionConfig(workspace=Workspace(path=tmp_path)), _BASH_COMMAND)
    assert env["SHELL"] == _BASH_COMMAND
    assert env["BASH"] == _BASH_COMMAND


def test_build_bash_env_set_env_can_override_workspace_root(tmp_path: Path) -> None:
    env = build_bash_env(SessionConfig(
        workspace=Workspace(path=tmp_path), set_env={"WORKSPACE_ROOT": "/overridden"}), _BASH_COMMAND)
    assert env["WORKSPACE_ROOT"] == "/overridden"


def test_build_bash_env_set_env_can_override_shell_and_bash(tmp_path: Path) -> None:
    env = build_bash_env(SessionConfig(
        workspace=Workspace(path=tmp_path),
        set_env={"SHELL": "/overridden/shell", "BASH": "/overridden/bash"}), _BASH_COMMAND)
    assert env["SHELL"] == "/overridden/shell"
    assert env["BASH"] == "/overridden/bash"


def test_build_bash_env_shares_configured_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MY_TOOLCHAIN_VAR", "abc")
    env = build_bash_env(
        SessionConfig(workspace=Workspace(path=tmp_path), share_env=["MY_TOOLCHAIN_VAR"]), _BASH_COMMAND)
    assert env.get("MY_TOOLCHAIN_VAR") == "abc"


def test_build_bash_env_set_env_overrides_shared_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FOO", "from-process")
    env = build_bash_env(SessionConfig(
        workspace=Workspace(path=tmp_path), share_env=["FOO"], set_env={"FOO": "overridden"}), _BASH_COMMAND)
    assert env["FOO"] == "overridden"


def test_build_bash_env_does_not_share_unlisted_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SOME_OTHER_VAR", "value")
    env = build_bash_env(SessionConfig(workspace=Workspace(path=tmp_path)), _BASH_COMMAND)
    assert "SOME_OTHER_VAR" not in env


def test_share_env_variable_is_visible_to_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Single-quoted inner command so $KLORB_TEST_VAR is only expanded once bash actually runs
    it (with the env we built), not by shfmt/the outer shell's own parse-time interpolation."""
    monkeypatch.setenv("KLORB_TEST_VAR", "visible-value")
    context = _context(tmp_path, command_rules=CommandRules(allow=[["bash", "**"]]))
    context.session_config.share_env = ["KLORB_TEST_VAR"]
    tool = BashTool(context)
    result = _apply(tool, "bash -c 'echo $KLORB_TEST_VAR'")
    assert result["stdout"] == "visible-value\n"


def test_unshared_variable_is_not_visible_to_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KLORB_TEST_SECRET", "should-not-leak")
    context = _context(tmp_path, command_rules=CommandRules(allow=[["bash", "**"]]))
    tool = BashTool(context)
    result = _apply(tool, "bash -c 'echo $KLORB_TEST_SECRET'")
    assert result["stdout"] == "\n"


# --- session-scoped persistent shells (shell_lifetime="session"/"new") ---


def _persistent_context(tmp_path: Path, **kwargs: Any) -> ToolSetupContext:
    return _context(tmp_path, command_rules=CommandRules(allow=[["**"]]), **kwargs)


def _run_session(tool: BashTool, command: str, shell_lifetime: str = "session") -> Any:
    return tool.apply({"command": command, "intent": "test intent", "shell_lifetime": shell_lifetime})


def test_cd_and_env_persist_across_session_calls(tmp_path: Path) -> None:
    context = _persistent_context(tmp_path)
    subdir = tmp_path / "subdir"
    subdir.mkdir()

    tool1 = BashTool(context)
    _run_session(tool1, f"cd {subdir} && export KLORB_TEST_VAR=persisted")

    tool2 = BashTool(context)
    result = _run_session(tool2, "pwd && printenv KLORB_TEST_VAR")

    assert result["success"] is True
    assert result["stdout"] == f"{subdir}\npersisted\n"
    assert result["terminal_alive"] is True
    assert result["terminal_cwd"] == str(subdir)


def test_session_lifetime_reuses_the_same_shell_process(tmp_path: Path) -> None:
    context = _persistent_context(tmp_path)
    tool1 = BashTool(context)
    pid1 = _run_session(tool1, "bash -c 'echo $PPID'")["stdout"].strip()
    tool2 = BashTool(context)
    pid2 = _run_session(tool2, "bash -c 'echo $PPID'")["stdout"].strip()
    assert pid1 == pid2


def test_new_lifetime_kills_the_prior_persistent_shell(tmp_path: Path) -> None:
    context = _persistent_context(tmp_path)
    tool1 = BashTool(context)
    pid1 = _run_session(tool1, "bash -c 'echo $PPID'")["stdout"].strip()

    tool2 = BashTool(context)
    result = _run_session(tool2, "bash -c 'echo $PPID'", shell_lifetime="new")
    pid2 = result["stdout"].strip()

    assert pid1 != pid2
    assert result["terminal_alive"] is True


def test_timeout_kills_the_whole_persistent_shell(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    process_config.bash_timeout_seconds = 0.5
    context = _persistent_context(tmp_path, process_config=process_config)
    tool = BashTool(context)

    result = _run_session(tool, "sleep 5")

    assert result["success"] is False
    assert "timed out" in result["failure_reason"]
    assert result["terminal_alive"] is False
    assert result["terminal_cwd"] is None

    # A following shell_lifetime="session" call transparently creates a new shell.
    tool2 = BashTool(context)
    revived = _run_session(tool2, "echo revived")
    assert revived["success"] is True
    assert revived["terminal_alive"] is True
    assert revived["stdout"] == "revived\n"


def test_cancel_event_sends_sigint_to_a_running_persistent_shell_command(tmp_path: Path) -> None:
    """Setting `Session.active_cancel_event` while a `shell_lifetime="session"` command is
    running sends `SIGINT` to it immediately, same as the one-shot path — see
    `PersistentShell._run_raw`. The whole non-interactive shell (not just `sleep`) dies to
    `SIGINT` too, the same as it already does for a plain command's timeout (see
    `test_timeout_kills_the_whole_persistent_shell`), so `terminal_alive` is `False`
    afterward."""
    process_config = ProcessConfig()
    process_config.bash_timeout_seconds = 30.0
    context = _persistent_context(tmp_path, process_config=process_config)
    assert context.session is not None
    context.session.active_cancel_event = threading.Event()
    tool = BashTool(context)

    def _cancel_soon() -> None:
        time.sleep(0.3)
        assert context.session is not None
        assert context.session.active_cancel_event is not None
        context.session.active_cancel_event.set()

    threading.Thread(target=_cancel_soon, daemon=True).start()

    start = time.monotonic()
    result = _run_session(tool, "sleep 30")
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    assert result["success"] is False
    assert "canceled" in result["failure_reason"].lower()
    assert "SIGINT Triggered by user" in result["stderr"]
    assert result["terminal_alive"] is False


def test_exit_inside_persistent_shell_reports_dead_terminal(tmp_path: Path) -> None:
    context = _persistent_context(tmp_path)
    tool1 = BashTool(context)
    _run_session(tool1, "true")  # create the shell

    tool2 = BashTool(context)
    result = _run_session(tool2, "exit 3")

    assert result["terminal_alive"] is False
    assert result["success"] is False
    assert context.session is not None
    assert context.session.tool_state["Bash"]["persistent_shell"] is None


def test_session_lifetime_without_a_session_raises_value_error(tmp_path: Path) -> None:
    context = _persistent_context(tmp_path, with_session=False)
    tool = BashTool(context)
    with pytest.raises(ValueError, match="Session"):
        _run_session(tool, "true")


def test_session_output_over_spill_bytes_is_written_to_a_file(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    process_config.bash_spill_bytes = 10
    context = _persistent_context(tmp_path, process_config=process_config)
    tool = BashTool(context)

    result = _run_session(tool, "echo this-is-longer-than-ten-bytes")

    assert result["stdout"] is None
    assert result["stdout_file"] is not None
    assert Path(result["stdout_file"]).read_text().strip() == "this-is-longer-than-ten-bytes"


def test_session_registers_a_standing_interjection_while_alive(tmp_path: Path) -> None:
    context = _persistent_context(tmp_path)
    tool = BashTool(context)
    _run_session(tool, f"cd {tmp_path}")

    assert context.session is not None
    # pylint: disable-next=protected-access
    provider = context.session._standing_interjection_providers["SessionTerminal"]
    message = provider()
    assert message is not None
    assert "/tmp" in message
    assert 'shell_lifetime="session"' in message


def test_standing_interjection_disappears_once_the_shell_dies(tmp_path: Path) -> None:
    process_config = ProcessConfig()
    process_config.bash_timeout_seconds = 0.5
    context = _persistent_context(tmp_path, process_config=process_config)
    tool = BashTool(context)
    _run_session(tool, "sleep 5")

    assert context.session is not None
    # pylint: disable-next=protected-access
    provider = context.session._standing_interjection_providers["SessionTerminal"]
    assert provider() is None


def test_session_close_kills_a_live_persistent_shell(tmp_path: Path) -> None:
    context = _persistent_context(tmp_path)
    tool = BashTool(context)
    pid = _run_session(tool, "bash -c 'echo $PPID'")["stdout"].strip()

    assert context.session is not None
    context.session.close()

    with pytest.raises(ProcessLookupError):
        os.kill(int(pid), 0)


# --- summary() ---


def test_summary_leads_with_intent_ahead_of_the_command(tmp_path: Path) -> None:
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    args = {"command": "echo hi", "intent": "Say hi", "shell_lifetime": "command"}
    result = tool.apply(args)

    assert tool.summary(args, result) == f"Bash: Say hi\n$ echo hi\n(ok, {result['runtime']:.2f}s)"


def test_summary_falls_back_to_the_bare_command_with_no_intent(tmp_path: Path) -> None:
    """`summary()` reads `intent` defensively via `args.get()` rather than assuming it's always
    present, so a call whose `args` predate this field (or otherwise omit it) still renders a
    sensible summary instead of raising."""
    context = _context(tmp_path, command_rules=CommandRules(allow=[["echo", "*"]]))
    tool = BashTool(context)
    args = {"command": "echo hi", "intent": "Say hi", "shell_lifetime": "command"}
    result = tool.apply(args)
    args_without_intent = {key: value for key, value in args.items() if key != "intent"}

    assert tool.summary(args_without_intent, result) == f"Bash: echo hi\n(ok, {result['runtime']:.2f}s)"


# --- sandboxed execution (opt-in; needs a real, working bwrap) ---


def _use_real_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo `_unsandboxed_by_default` for this one test, restoring the real availability check so
    `BashTool` actually wraps its command in `bwrap`. Runs in the test body, after the autouse
    fixture, so it wins."""
    monkeypatch.setattr("klorb.tools.bash.bwrap_available", sandbox.bwrap_available)


@requires_bwrap
def test_no_sandbox_notice_when_actually_sandboxed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_real_sandbox(monkeypatch)
    context = _context(tmp_path, command_rules=CommandRules(allow=[["true"]]))
    result = _apply(BashTool(context), "true")
    assert result["success"] is True
    assert "sandbox_notice" not in result  # nothing to warn about -- it really was sandboxed


@requires_bwrap
def test_sandboxed_command_runs_under_the_fake_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_real_sandbox(monkeypatch)
    context = _context(tmp_path, command_rules=CommandRules(allow=[["hostname"]]))
    result = _apply(BashTool(context), "hostname")
    assert result["stdout"].strip() == sandbox.SANDBOX_HOSTNAME


@requires_bwrap
def test_sandboxed_command_cannot_read_a_denied_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end defense in depth: the OS-level mask hides a denied directory's contents even
    from a command the classifier *allowed* because the read isn't visible in its argv (`find`
    walking a tree, unlike `cat`/`ls`, isn't checked against readDirs). The mask is the only thing
    standing between the approved command and the secret here."""
    _use_real_sandbox(monkeypatch)
    secret_dir = tmp_path / "secretdir"
    secret_dir.mkdir()
    (secret_dir / "secret.txt").write_text("TOP SECRET")
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["find", "**"]]),
        read_dirs=DirRules(allow=[tmp_path], deny=[secret_dir]))
    result = _apply(BashTool(context), "find secretdir -type f")
    assert result["success"] is True
    assert "secret.txt" not in result["stdout"]  # masked -> find sees an empty directory


@requires_bwrap
def test_sandboxed_command_can_read_a_readfiles_allowed_file_outside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """An exact `readFiles.allow` grant for a file outside the workspace (and outside home) is
    both classified-allowed and actually bound into the sandbox, so the command can read it."""
    _use_real_sandbox(monkeypatch)
    outside = tmp_path_factory.mktemp("outside") / "token.conf"
    outside.write_text("ALLOWED-SECRET")
    context = _context(
        tmp_path, command_rules=CommandRules(allow=[["cat", "*"]]),
        read_dirs=DirRules(allow=[tmp_path]),
        read_files=FileRules(allow=[outside]))
    result = _apply(BashTool(context), f"cat {outside}")
    assert result["success"] is True
    assert result["stdout"].strip() == "ALLOWED-SECRET"


def _persistent_shell(context: ToolSetupContext) -> Any:
    assert context.session is not None
    return context.session.tool_state["Bash"]["persistent_shell"]


@requires_bwrap
def test_persistent_sandbox_rebuilds_and_preserves_state_when_a_grant_grows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory,
) -> None:
    _use_real_sandbox(monkeypatch)
    context = _persistent_context(tmp_path)
    tool = BashTool(context)
    # Establish exported-env and cwd state (a workspace subdir, so it's readable and survives the
    # rebuild -- the workspace is bound in both the old and new sandbox) that must carry over.
    _run_session(tool, "export KLORB_REBUILD_MARKER=survived; mkdir -p subdir; cd subdir")
    first_shell = _persistent_shell(context)

    # Grant a brand-new directory outside the workspace and home, so the allowed-dir snapshot
    # actually grows (a dir under either would already be covered and wouldn't widen it).
    granted = tmp_path_factory.mktemp("granted")
    context.session_config.read_dirs = DirRules(allow=[tmp_path, granted])

    # printenv reads the exported var with a literal argument (echo "$VAR" would be a non-literal
    # token the classifier force-asks on, which is beside the point here).
    result = _run_session(tool, "printenv KLORB_REBUILD_MARKER")
    assert result["sandbox_rebuilt"] is True
    assert _persistent_shell(context) is not first_shell  # a fresh sandbox replaced the stale one
    assert result["stdout"].strip() == "survived"  # exported env carried over
    assert result["terminal_cwd"] is not None
    assert result["terminal_cwd"].endswith("subdir")  # cwd carried over


@requires_bwrap
def test_persistent_sandbox_does_not_rebuild_when_the_grant_set_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_real_sandbox(monkeypatch)
    context = _persistent_context(tmp_path)
    tool = BashTool(context)
    _run_session(tool, "echo one")
    first_shell = _persistent_shell(context)
    result = _run_session(tool, "echo two")
    assert result["sandbox_rebuilt"] is False
    assert _persistent_shell(context) is first_shell  # same process, full state persistence


@requires_bwrap
def test_persistent_sandbox_refuses_to_rebuild_over_live_background_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """When a grant grows but the sandboxed shell has live background jobs, the command is not
    run and the shell is left untouched -- the model must opt into a shell_lifetime="new" respawn
    rather than have the harness silently kill its background work."""
    _use_real_sandbox(monkeypatch)
    context = _persistent_context(tmp_path)
    tool = BashTool(context)
    _run_session(tool, "echo start")
    shell = _persistent_shell(context)
    # Plant a real background job directly on the shell (a top-level '&' would be force-asked by
    # the classifier, which is beside the point of what this test exercises).
    shell.run_command("sleep 300 &", 120.0)
    assert shell.has_live_jobs()

    granted = tmp_path_factory.mktemp("granted")
    context.session_config.read_dirs = DirRules(allow=[tmp_path, granted])

    result = _run_session(tool, "echo after")
    assert result["success"] is False
    assert result["sandbox_rebuilt"] is False
    assert result["terminal_alive"] is True
    assert "shell_lifetime" in result["failure_reason"]
    assert _persistent_shell(context) is shell  # same shell, still alive, command never ran
