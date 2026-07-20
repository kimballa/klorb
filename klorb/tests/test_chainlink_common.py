# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.tasks.common."""

import subprocess
from pathlib import Path

import pytest

from klorb.process_config import ProcessConfig
from klorb.session import Session, SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.tasks import common as tasks_common
from klorb.tools.tasks.common import ChainlinkClient, ChainlinkError, chainlink_available, open_blocker_count
from klorb.workspace import Workspace

requires_chainlink = pytest.mark.skipif(
    not chainlink_available(),
    reason="chainlink binary not found on PATH or ~/.cargo/bin")


def _context(tmp_path: Path, session: Session | None) -> ToolSetupContext:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(workspace=Workspace(path=workspace_root, trusted=True)),
        session=session)


def _session(tmp_path: Path, session_id: str = "2026-07-20-00-00-test-label") -> Session:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    config = SessionConfig(workspace=Workspace(path=workspace_root, trusted=True))
    return Session(config=config, session_id=session_id)


@requires_chainlink
def test_construction_requires_a_session(tmp_path: Path) -> None:
    context = _context(tmp_path, session=None)

    with pytest.raises(ValueError, match="requires a Session"):
        ChainlinkClient(context)


def test_binary_discovery_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks_common.shutil, "which", lambda name: "/usr/bin/chainlink")
    assert tasks_common._discover_binary() == Path("/usr/bin/chainlink")


def test_binary_discovery_falls_back_to_cargo_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks_common.shutil, "which", lambda name: None)
    fake_home = tmp_path / "home"
    (fake_home / ".cargo" / "bin").mkdir(parents=True)
    fallback = fake_home / ".cargo" / "bin" / "chainlink"
    fallback.write_text("#!/bin/sh\n")
    monkeypatch.setattr(tasks_common.Path, "home", lambda: fake_home)

    assert tasks_common._discover_binary() == fallback


def test_binary_discovery_returns_none_when_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks_common.shutil, "which", lambda name: None)
    monkeypatch.setattr(tasks_common.Path, "home", lambda: tmp_path / "empty-home")

    assert tasks_common._discover_binary() is None
    assert not tasks_common.chainlink_available()


def test_open_blocker_count_only_counts_ids_still_open() -> None:
    issue = {"id": 5, "blocked_by": [1, 2, 3]}
    assert open_blocker_count(issue, open_ids={1, 3}) == 2
    assert open_blocker_count(issue, open_ids=set()) == 0


@requires_chainlink
def test_run_retries_on_lock_contention_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path)
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

    result = client._run(["--json", "issue", "list"])

    assert result.stdout == "ok"
    assert len(calls) == 3


@requires_chainlink
def test_run_raises_chainlink_error_after_exhausting_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    def always_locked(command, cwd, capture_output, text, env):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Error: database is locked\n")

    monkeypatch.setattr(subprocess, "run", always_locked)
    monkeypatch.setattr(tasks_common.time, "sleep", lambda seconds: None)

    with pytest.raises(ChainlinkError, match="database is locked"):
        client._run(["--json", "issue", "list"])


@requires_chainlink
def test_run_does_not_retry_a_non_lock_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    calls: list[list[str]] = []

    def fake_run(command, cwd, capture_output, text, env):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Error: not a chainlink repository")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ChainlinkError, match="not a chainlink repository"):
        client._run(["--json", "issue", "list"])
    assert len(calls) == 1


@requires_chainlink
def test_ensure_setup_prunes_claude_scaffold_it_created(tmp_path: Path) -> None:
    session = _session(tmp_path)
    context = _context(tmp_path, session)
    workspace_root = context.session_config.workspace.path

    ChainlinkClient(context)

    assert (workspace_root / ".chainlink" / "issues.db").exists()
    assert not (workspace_root / ".claude").exists()
    assert not (workspace_root / ".mcp.json").exists()


@requires_chainlink
def test_ensure_setup_preserves_a_pre_existing_claude_settings_file(tmp_path: Path) -> None:
    session = _session(tmp_path)
    context = _context(tmp_path, session)
    workspace_root = context.session_config.workspace.path
    claude_dir = workspace_root / ".claude"
    claude_dir.mkdir(parents=True)
    settings_path = claude_dir / "settings.json"
    settings_path.write_text('{"myOwnSetting": true}')

    ChainlinkClient(context)

    assert settings_path.read_text() == '{"myOwnSetting": true}'


@requires_chainlink
def test_ensure_setup_adds_gitignore_entry(tmp_path: Path) -> None:
    session = _session(tmp_path)
    context = _context(tmp_path, session)
    workspace_root = context.session_config.workspace.path

    ChainlinkClient(context)

    gitignore_lines = (workspace_root / ".gitignore").read_text().splitlines()
    assert ".chainlink/" in gitignore_lines


@requires_chainlink
def test_ensure_setup_does_not_duplicate_an_existing_gitignore_entry(tmp_path: Path) -> None:
    session = _session(tmp_path)
    context = _context(tmp_path, session)
    workspace_root = context.session_config.workspace.path
    (workspace_root / ".gitignore").write_text("node_modules/\n.chainlink/\n")

    ChainlinkClient(context)

    gitignore_lines = (workspace_root / ".gitignore").read_text().splitlines()
    assert gitignore_lines.count(".chainlink/") == 1


@requires_chainlink
def test_ensure_setup_is_a_cheap_noop_once_the_database_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session(tmp_path)
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
def test_create_show_update_close_round_trip(tmp_path: Path) -> None:
    session = _session(tmp_path)
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
def test_block_and_unblock_round_trip(tmp_path: Path) -> None:
    session = _session(tmp_path)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)

    blocked_id = client.create_issue("Ship it")
    blocker_id = client.create_issue("Write tests first")

    client.block(blocked_id, blocker_id)
    assert client.show_issue(blocked_id)["blocked_by"] == [blocker_id]

    client.unblock(blocked_id, blocker_id)
    assert client.show_issue(blocked_id)["blocked_by"] == []


@requires_chainlink
def test_list_issues_is_scoped_to_this_sessions_label(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    config = SessionConfig(workspace=Workspace(path=workspace_root, trusted=True))

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
def test_close_all_on_teardown_closes_this_labels_open_issues(tmp_path: Path) -> None:
    session = _session(tmp_path)
    context = _context(tmp_path, session)
    client = ChainlinkClient(context)
    issue_id = client.create_issue("Will be closed on session close")

    session.close()

    assert client.show_issue(issue_id)["status"] == "closed"
