# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tasks.common."""
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig, WorkspaceAccess
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks import common as tasks_common
from klorb.tools.tasks.common import ChainlinkClient, ChainlinkError, chainlink_available, open_blocker_count
from klorb.workspace import Workspace

requires_chainlink = pytest.mark.skipif(
    not chainlink_available(),
    reason="chainlink binary not found in vnd/, on PATH, or at ~/.cargo/bin")


def _context(tmp_path: Path, session: Session | None) -> ToolSetupContext:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(
            workspace_access=WorkspaceAccess(workspace=Workspace(path=workspace_root, trusted=True))),
        session=session)


def _session(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
    session_id: str = "2026-07-20-00-00-test-label"
) -> Session:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    config = make_session_config(workspace=Workspace(path=workspace_root, trusted=True))
    return Session(config=config, session_id=session_id)


@requires_chainlink
def test_construction_requires_a_session(tmp_path: Path) -> None:
    context = _context(tmp_path, session=None)

    with pytest.raises(ValueError, match="requires a Session"):
        ChainlinkClient(context)


def test_binary_discovery_prefers_vnd_over_everything_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks_common.reset_cached_chainlink_path()
    vnd_dir = tmp_path / "vnd"
    (vnd_dir / "x86_64-unknown-linux-gnu").mkdir(parents=True)
    vnd_chainlink = vnd_dir / "x86_64-unknown-linux-gnu" / "chainlink"
    vnd_chainlink.write_text("#!/bin/sh\n")
    monkeypatch.setattr(tasks_common, "_vnd_dir", lambda: vnd_dir)
    monkeypatch.setattr(tasks_common, "_rust_target_triple", lambda: "x86_64-unknown-linux-gnu")
    monkeypatch.setattr(tasks_common.shutil, "which", lambda name: "/usr/bin/chainlink")

    assert tasks_common._discover_binary() == vnd_chainlink


def test_binary_discovery_uses_the_rust_target_triple_as_the_vnd_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks_common.reset_cached_chainlink_path()
    vnd_dir = tmp_path / "vnd"
    (vnd_dir / "aarch64-unknown-linux-gnu").mkdir(parents=True)
    vnd_chainlink = vnd_dir / "aarch64-unknown-linux-gnu" / "chainlink"
    vnd_chainlink.write_text("#!/bin/sh\n")
    monkeypatch.setattr(tasks_common, "_vnd_dir", lambda: vnd_dir)
    monkeypatch.setattr(tasks_common, "_rust_target_triple", lambda: "aarch64-unknown-linux-gnu")

    assert tasks_common._discover_binary() == vnd_chainlink


def test_rust_target_triple_uses_apple_darwin_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks_common.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tasks_common.platform, "machine", lambda: "arm64")

    assert tasks_common._rust_target_triple() == "aarch64-apple-darwin"


def test_binary_discovery_prefers_virtual_env_over_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks_common.reset_cached_chainlink_path()
    monkeypatch.setattr(tasks_common, "_vnd_dir", lambda: None)
    monkeypatch.setattr(tasks_common.shutil, "which", lambda name: "/usr/bin/chainlink")
    venv_dir = tmp_path / "venv"
    (venv_dir / "bin").mkdir(parents=True)
    venv_chainlink = venv_dir / "bin" / "chainlink"
    venv_chainlink.write_text("#!/bin/sh\n")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv_dir))

    assert tasks_common._discover_binary() == venv_chainlink


def test_binary_discovery_prefers_path_over_cargo_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks_common.reset_cached_chainlink_path()
    monkeypatch.setattr(tasks_common, "_vnd_dir", lambda: None)
    monkeypatch.setattr(tasks_common.shutil, "which", lambda name: "/usr/bin/chainlink")
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    fake_home = tmp_path / "home"
    (fake_home / ".cargo" / "bin").mkdir(parents=True)
    (fake_home / ".cargo" / "bin" / "chainlink").write_text("#!/bin/sh\n")
    monkeypatch.setattr(tasks_common.Path, "home", lambda: fake_home)

    assert tasks_common._discover_binary() == Path("/usr/bin/chainlink")


def test_binary_discovery_falls_back_to_cargo_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks_common.reset_cached_chainlink_path()
    monkeypatch.setattr(tasks_common, "_vnd_dir", lambda: None)
    monkeypatch.setattr(tasks_common.shutil, "which", lambda name: None)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    fake_home = tmp_path / "home"
    (fake_home / ".cargo" / "bin").mkdir(parents=True)
    fallback = fake_home / ".cargo" / "bin" / "chainlink"
    fallback.write_text("#!/bin/sh\n")
    monkeypatch.setattr(tasks_common.Path, "home", lambda: fake_home)

    assert tasks_common._discover_binary() == fallback


def test_binary_discovery_returns_none_when_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tasks_common.reset_cached_chainlink_path()
    monkeypatch.setattr(tasks_common, "_vnd_dir", lambda: None)
    monkeypatch.setattr(tasks_common.shutil, "which", lambda name: None)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(tasks_common.Path, "home", lambda: tmp_path / "empty-home")

    assert tasks_common._discover_binary() is None
    assert not tasks_common.chainlink_available()


def test_open_blocker_count_reads_the_blocked_by_open_field() -> None:
    assert open_blocker_count({"id": 5, "blocked_by_open": [1, 3]}) == 2
    assert open_blocker_count({"id": 5, "blocked_by_open": []}) == 0
    assert open_blocker_count({"id": 5}) == 0


@requires_chainlink
def test_run_retries_on_lock_contention_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    calls: list[list[str]] = []

    def fake_run(command, cwd, capture_output, text, env):
        calls.append(command)
        if len(calls) < 3:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Error: database is locked")
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(tasks_common.time, "sleep", lambda seconds: None)

    result = client._run(["issue", "list"])

    assert result.stdout == "ok"
    assert len(calls) == 3


@requires_chainlink
def test_run_raises_chainlink_error_after_exhausting_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    def always_locked(command, cwd, capture_output, text, env):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Error: database is locked\n")

    monkeypatch.setattr(subprocess, "run", always_locked)
    monkeypatch.setattr(tasks_common.time, "sleep", lambda seconds: None)

    with pytest.raises(ChainlinkError, match="database is locked"):
        client._run(["issue", "list"])


@requires_chainlink
def test_run_does_not_retry_a_non_lock_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    calls: list[list[str]] = []

    def fake_run(command, cwd, capture_output, text, env):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Error: not a chainlink repository")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ChainlinkError, match="not a chainlink repository"):
        client._run(["issue", "list"])
    assert len(calls) == 1


@requires_chainlink
def test_ensure_setup_uses_db_only_and_plants_no_claude_scaffold(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    workspace_root = context.session_config.workspace.path

    ChainlinkClient(context)

    assert (workspace_root / ".chainlink" / "issues.db").exists()
    assert not (workspace_root / ".claude").exists()
    assert not (workspace_root / ".mcp.json").exists()


@requires_chainlink
def test_ensure_setup_adds_gitignore_entry(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    workspace_root = context.session_config.workspace.path

    ChainlinkClient(context)

    gitignore_lines = (workspace_root / ".gitignore").read_text().splitlines()
    assert ".chainlink/" in gitignore_lines


@requires_chainlink
def test_ensure_setup_does_not_duplicate_an_existing_gitignore_entry(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    workspace_root = context.session_config.workspace.path
    (workspace_root / ".gitignore").write_text("node_modules/\n.chainlink/\n")

    ChainlinkClient(context)

    gitignore_lines = (workspace_root / ".gitignore").read_text().splitlines()
    assert gitignore_lines.count(".chainlink/") == 1


@requires_chainlink
def test_ensure_setup_is_a_cheap_noop_once_the_database_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    ChainlinkClient(context)  # first call does the real init

    called = False
    real_run = subprocess.run

    def spy_run(*args, **kwargs):
        nonlocal called
        if "init" in args[0]:
            called = True
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy_run)
    ChainlinkClient(context)  # second call must not re-run init

    assert not called


@requires_chainlink
def test_create_show_update_close_round_trip(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    new_id = client.create_issue("Write the spec", description="details", priority="high")
    issue = client.show_issue(new_id)
    assert issue["title"] == "Write the spec"
    assert issue["description"] == "details"
    assert issue["priority"] == "high"
    assert issue["status"] == "open"

    client.update_issue(new_id, title="Write the spec (revised)")
    assert client.show_issue(new_id)["title"] == "Write the spec (revised)"

    client.comment(new_id, "made progress")
    assert client.show_issue(new_id)["comments"][0]["content"] == "made progress"

    client.close_issue(new_id)
    assert client.show_issue(new_id)["status"] == "closed"
    assert not (context.session_config.workspace.path / "CHANGELOG.md").exists()

    client.reopen_issue(new_id)
    assert client.show_issue(new_id)["status"] == "open"


@requires_chainlink
def test_block_and_unblock_round_trip(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    blocked_id = client.create_issue("Ship it")
    blocker_id = client.create_issue("Write tests first")

    client.block(blocked_id, blocker_id)
    assert client.show_issue(blocked_id)["blocked_by"] == [blocker_id]

    client.unblock(blocked_id, blocker_id)
    assert client.show_issue(blocked_id)["blocked_by"] == []


@requires_chainlink
def test_list_issues_is_scoped_to_this_sessions_label(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = make_session_config(workspace=Workspace(path=workspace_root, trusted=True))

    session_a = Session(config=config, session_id="label-a")
    context_a = ToolSetupContext(
        process_config=ProcessConfig(), session_config=config, session=session_a)
    client_a = ChainlinkClient(context_a)
    client_a.create_issue("Issue under label a")

    session_b = Session(config=config, session_id="label-b")
    context_b = ToolSetupContext(
        process_config=ProcessConfig(), session_config=config, session=session_b)
    client_b = ChainlinkClient(context_b)
    client_b.create_issue("Issue under label b")

    titles_a = {issue["title"] for issue in client_a.list_issues()}
    titles_b = {issue["title"] for issue in client_b.list_issues()}
    assert titles_a == {"Issue under label a"}
    assert titles_b == {"Issue under label b"}


@requires_chainlink
def test_list_issues_extra_label_ands_with_the_client_label(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)
    client.create_issue("Mine and marked", extra_label="marked")
    client.create_issue("Mine but unmarked")

    titles = {issue["title"] for issue in client.list_issues(extra_label="marked")}

    assert titles == {"Mine and marked"}


@requires_chainlink
def test_close_all_on_teardown_closes_this_labels_open_issues(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)
    issue_id = client.create_issue("Will be closed on session close")

    session.close()

    assert client.show_issue(issue_id)["status"] == "closed"


@requires_chainlink
def test_teardown_is_only_registered_for_a_root_session(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = make_session_config(workspace=Workspace(path=workspace_root, trusted=True))
    root = Session(config=config, session_id="root-id")
    child = Session(config=config, session_id="child-id", parent=root, root_id=root.root_id)
    child_context = ToolSetupContext(process_config=ProcessConfig(),
                                     session_config=config, session=child)

    ChainlinkClient(child_context)

    assert "ChainlinkClient" not in child._teardown_callbacks
    assert "ChainlinkClient" not in root._teardown_callbacks


def _root_session_with_process_config(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> Session:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    config = make_session_config(workspace=Workspace(path=workspace_root, trusted=True))
    return Session(config=config, process_config=ProcessConfig())


@requires_chainlink
def test_ensure_chainlink_client_registers_teardown_for_a_root_session(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _root_session_with_process_config(tmp_path, make_session_config)

    session.ensure_chainlink_client()

    assert "ChainlinkClient" in session._teardown_callbacks
    assert session.tool_state["tasks"]["client"] is not None


@requires_chainlink
def test_ensure_chainlink_client_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _root_session_with_process_config(tmp_path, make_session_config)

    calls: list[None] = []
    real_init = ChainlinkClient.__init__

    def counting_init(self, ctx):  # type: ignore[no-untyped-def]
        calls.append(None)
        real_init(self, ctx)

    monkeypatch.setattr(ChainlinkClient, "__init__", counting_init)

    session.ensure_chainlink_client()
    session.ensure_chainlink_client()

    assert len(calls) == 1


def test_ensure_chainlink_client_is_a_noop_without_a_process_config(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)

    session.ensure_chainlink_client()  # no raise, no attempt

    assert "tasks" not in session.tool_state


@requires_chainlink
def test_add_label_and_remove_label_round_trip(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)
    issue_id = client.create_issue("Label me")

    assert client.add_label(issue_id, "agent:someone") is True
    assert "agent:someone" in client.show_issue(issue_id)["labels"]

    assert client.remove_label(issue_id, "agent:someone") is True
    assert "agent:someone" not in client.show_issue(issue_id)["labels"]


@requires_chainlink
def test_add_label_is_idempotent(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)
    issue_id = client.create_issue("Label me twice")

    assert client.add_label(issue_id, "agent:someone") is True
    assert client.add_label(issue_id, "agent:someone") is False  # already there

    assert client.show_issue(issue_id)["labels"].count("agent:someone") == 1


@requires_chainlink
def test_remove_label_is_a_noop_when_absent(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)
    issue_id = client.create_issue("Nothing to remove")

    assert client.remove_label(issue_id, "agent:never-added") is False


@requires_chainlink
def test_fetch_and_sort_issues_is_a_chainlink_client_method(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)
    client.create_issue("Only issue")

    issues = client.fetch_and_sort_issues(include_closed=False)

    assert [issue["title"] for issue in issues] == ["Only issue"]


@requires_chainlink
def test_client_scopes_issues_by_root_id_not_by_session_id(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = make_session_config(workspace=Workspace(path=workspace_root, trusted=True))

    session = Session(config=config, session_id="child-id", root_id="shared-root")
    context = ToolSetupContext(process_config=ProcessConfig(), session_config=config, session=session)
    client = ChainlinkClient(context)
    client.create_issue("Issue under the shared root")

    titles = {issue["title"] for issue in client.list_issues()}
    assert titles == {"Issue under the shared root"}


def test_validate_priority_accepts_every_known_priority() -> None:
    for priority in ("low", "medium", "high", "critical"):
        tasks_common.validate_priority(priority)  # no raise


def test_validate_priority_rejects_unknown_priority() -> None:
    with pytest.raises(ValueError, match="priority must be one of"):
        tasks_common.validate_priority("urgent")


@requires_chainlink
def test_create_issue_rejects_invalid_priority_before_shelling_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess.run should not be called for an invalid priority")

    monkeypatch.setattr(subprocess, "run", fail_if_called)

    with pytest.raises(ValueError, match="priority must be one of"):
        client.create_issue("Task", priority="urgent")  # type: ignore[arg-type]


@requires_chainlink
def test_update_issue_rejects_invalid_priority(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)
    issue_id = client.create_issue("Task")

    with pytest.raises(ValueError, match="priority must be one of"):
        client.update_issue(issue_id, priority="urgent")  # type: ignore[arg-type]


@requires_chainlink
def test_run_always_passes_json_and_respects_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_session_config: Callable[..., SessionConfig],
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    captured: list[list[str]] = []

    def fake_run(command, cwd, capture_output, text, env):
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    client._run(["issue", "list"])
    assert "--json" in captured[-1]
    assert "--quiet" not in captured[-1]

    client._run(["issue", "close", "1"], quiet=True)
    assert "--json" in captured[-1]
    assert "--quiet" in captured[-1]


@requires_chainlink
def test_chainlink_error_message_includes_argv_cwd_and_exit_code(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    session = _session(tmp_path, make_session_config)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    with pytest.raises(ChainlinkError) as excinfo:
        client.show_issue(999999)

    message = str(excinfo.value)
    assert "issue" in message
    assert "show" in message
    assert "999999" in message
    assert str(context.session_config.workspace.path) in message
