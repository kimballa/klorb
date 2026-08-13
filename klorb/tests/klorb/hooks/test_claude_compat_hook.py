# © Copyright 2026 Aaron Kimball
"""Tests for the packaged `claude-compat` onSessionStart hook script
(`klorb.resources/data/hooks/claude-compat/check.py`), run through `run_bash_handler` the same
way `klorb.resources/default-config.json`'s `hooks.onSessionStart` entry invokes it.
"""

import importlib.resources
from pathlib import Path

import pytest

from klorb.hooks.bash_handler import run_bash_handler
from klorb.hooks.config import HookConfig
from klorb.hooks.hook_api import HookInput
from klorb.permissions.directory_access import DirRules
from klorb.session.config import SessionConfig
from klorb.workspace import Workspace

_SCRIPT_PATH = str(
    importlib.resources.files("klorb.resources").joinpath(
        "data", "hooks", "claude-compat", "check.py"))


@pytest.fixture(autouse=True)
def _unsandboxed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("klorb.hooks.bash_handler.bwrap_available", lambda: False)


def _run(
    workspace_root: Path, *, just_bootstrapped: bool, trusted: bool,
    config: dict[str, object] | None = None,
) -> str | None:
    handler = HookConfig(type="bash", name="claude-compat", command=["python3", _SCRIPT_PATH])
    hook_input = HookInput(
        hook="onSessionStart", reason="NewSession", workspace_root=str(workspace_root),
        workspace_just_bootstrapped=just_bootstrapped, workspace_trusted=trusted, config=config)
    session_config = SessionConfig(
        workspace=Workspace(path=workspace_root, trusted=trusted),
        read_dirs=DirRules(allow=[workspace_root]), write_dirs=DirRules(allow=[workspace_root]))
    result = run_bash_handler(
        handler, hook_input, session_config=session_config, bash_command="/bin/bash",
        timeout_seconds=5.0)
    assert result is not None
    return result.log


def test_suggests_claude_markdown_when_claude_md_present(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# hi")
    log = _run(tmp_path, just_bootstrapped=True, trusted=True)
    assert log is not None
    assert "compatibility.claudeMarkdown" in log
    assert "compatibility.claudeSkills" not in log


def test_suggests_claude_skills_when_claude_skills_dir_present(tmp_path: Path) -> None:
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    log = _run(tmp_path, just_bootstrapped=True, trusted=True)
    assert log is not None
    assert "compatibility.claudeSkills" in log
    assert "compatibility.claudeMarkdown" not in log


def test_suggests_both_flags_when_both_present(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# hi")
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    log = _run(tmp_path, just_bootstrapped=True, trusted=True)
    assert log is not None
    assert "compatibility.claudeMarkdown" in log
    assert "compatibility.claudeSkills" in log


def test_silent_when_neither_file_present(tmp_path: Path) -> None:
    assert _run(tmp_path, just_bootstrapped=True, trusted=True) is None


def test_silent_when_not_just_bootstrapped(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# hi")
    assert _run(tmp_path, just_bootstrapped=False, trusted=True) is None


def test_silent_when_workspace_not_trusted(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# hi")
    assert _run(tmp_path, just_bootstrapped=True, trusted=False) is None


def test_omits_markdown_flag_already_enabled(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# hi")
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    log = _run(
        tmp_path, just_bootstrapped=True, trusted=True,
        config={"compatibility_claude_markdown": True})
    assert log is not None
    assert "compatibility.claudeMarkdown" not in log
    assert "compatibility.claudeSkills" in log


def test_silent_when_both_flags_already_enabled(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# hi")
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    log = _run(
        tmp_path, just_bootstrapped=True, trusted=True,
        config={"compatibility_claude_markdown": True, "compatibility_claude_skills": True})
    assert log is None
