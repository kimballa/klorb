# © Copyright 2026 Aaron Kimball
"""Tests for the skill tools: SearchSkills, ActivateSkill, ReadSkillFile."""

from pathlib import Path

import pytest

from klorb.permissions.skill_access import SkillRules
from klorb.permissions.table import PermissionAskRequired, PermissionOverride
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.skill import common as skill_common
from klorb.tools.skill.activate_skill import ActivateSkillTool
from klorb.tools.skill.read_skill_file import ReadSkillFileTool
from klorb.tools.skill.search_skills import SearchSkillsTool
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _empty_internal_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    internal = tmp_path / "internal-skills"
    internal.mkdir()
    monkeypatch.setattr(skill_common, "internal_skills_dir", lambda: internal)
    monkeypatch.setattr(skill_common, "KLORB_DATA_DIR", tmp_path / "data")


def _write_skill(base: Path, name: str, description: str, *, body: str = "instructions") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\ndescription: {description}\n---\n\n{body}\n")
    return skill_dir


def _context(
    tmp_path: Path, *, skill_rules: SkillRules | None = None,
    override: PermissionOverride | None = None, claude_skills: bool = False,
) -> ToolSetupContext:
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    return ToolSetupContext(
        process_config=ProcessConfig(compatibility_claude_skills=claude_skills),
        session_config=SessionConfig(
            workspace=Workspace(path=ws, trusted=True),
            skill_rules=skill_rules if skill_rules is not None else SkillRules()),
        permission_override=override)


def _workspace_skills_dir(context: ToolSetupContext) -> Path:
    return context.session_config.workspace.path / ".klorb" / "skills"


# --- SearchSkills ---


def test_search_matches_name_and_body(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _write_skill(_workspace_skills_dir(context), "birds", "about birds", body="tweet tweet")
    _write_skill(_workspace_skills_dir(context), "cars", "about cars", body="vroom")

    result = SearchSkillsTool(context).apply({"queries": ["bird"]})
    assert result["match_count"] == 1
    assert result["results"][0]["name"] == "birds"
    assert result["results"][0]["description"] == "about birds"

    # Match against body text.
    body_hit = SearchSkillsTool(context).apply({"queries": ["vroom"]})
    assert [r["name"] for r in body_hit["results"]] == ["cars"]


def test_search_excludes_denied_skill(tmp_path: Path) -> None:
    context = _context(tmp_path, skill_rules=SkillRules(deny=[("workspace", "birds")]))
    _write_skill(_workspace_skills_dir(context), "birds", "about birds")
    result = SearchSkillsTool(context).apply({"queries": ["bird"]})
    assert result["match_count"] == 0


# --- ActivateSkill ---


def test_activate_allow_returns_content_and_manifest(tmp_path: Path) -> None:
    context = _context(tmp_path, skill_rules=SkillRules(allow=[("workspace", "s")]))
    skill_dir = _write_skill(_workspace_skills_dir(context), "s", "do the thing", body="step one")
    (skill_dir / "ref.md").write_text("reference")

    result = ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})
    assert result["namespace"] == "workspace"
    assert result["name"] == "s"
    assert "step one" in result["content"]
    assert result["files"] == ["SKILL.md", "ref.md"]
    assert isinstance(result["tokens"], int)
    assert result["tokens"] > 0


def test_activate_deny_raises_permission_error(tmp_path: Path) -> None:
    context = _context(tmp_path, skill_rules=SkillRules(deny=[("workspace", "s")]))
    _write_skill(_workspace_skills_dir(context), "s", "d")
    with pytest.raises(PermissionError):
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})


def test_activate_ask_raises_permission_ask_with_skill(tmp_path: Path) -> None:
    context = _context(tmp_path)  # empty rules -> ask
    _write_skill(_workspace_skills_dir(context), "s", "the description")
    with pytest.raises(PermissionAskRequired) as exc:
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})
    assert exc.value.skill == ("workspace", "s")
    assert "the description" in str(exc.value)


def test_activate_once_override_bypasses_ask(tmp_path: Path) -> None:
    override = PermissionOverride(skills=frozenset({("workspace", "s")}))
    context = _context(tmp_path, override=override)
    _write_skill(_workspace_skills_dir(context), "s", "d", body="loaded")
    result = ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})
    assert "loaded" in result["content"]


def test_activate_unknown_skill_raises_value_error(tmp_path: Path) -> None:
    context = _context(tmp_path)
    with pytest.raises(ValueError, match="no such skill"):
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "ghost"})


def test_activate_bad_name_raises_value_error(tmp_path: Path) -> None:
    context = _context(tmp_path)
    with pytest.raises(ValueError, match="skill name"):
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "../escape"})


# --- ReadSkillFile ---


def test_read_skill_file_allow(tmp_path: Path) -> None:
    context = _context(tmp_path, skill_rules=SkillRules(allow=[("workspace", "s")]))
    skill_dir = _write_skill(_workspace_skills_dir(context), "s", "d")
    (skill_dir / "ref.md").write_text("line1\nline2\nline3\n")

    result = ReadSkillFileTool(context).apply(
        {"namespace": "workspace", "name": "s", "path": "ref.md"})
    assert result["content"] == "1|line1\n2|line2\n3|line3"
    assert result["namespace"] == "workspace"
    assert result["name"] == "s"
    assert result["path"] == "ref.md"


def test_read_skill_file_gated_by_verdict(tmp_path: Path) -> None:
    # An un-activated ask-verdict skill raises the activation ask on ReadSkillFile too.
    context = _context(tmp_path)
    skill_dir = _write_skill(_workspace_skills_dir(context), "s", "d")
    (skill_dir / "ref.md").write_text("x")
    with pytest.raises(PermissionAskRequired) as exc:
        ReadSkillFileTool(context).apply({"namespace": "workspace", "name": "s", "path": "ref.md"})
    assert exc.value.skill == ("workspace", "s")


def test_read_internal_skill_file_via_resource_loader(tmp_path: Path) -> None:
    # An internal-tier skill's supporting file is read through the resource loader
    # (ReadFileCore.apply_readable), not the builtin open() — so it works even for a
    # zip/wheel-installed klorb whose packaged resources aren't plain filesystem paths.
    internal_dir = tmp_path / "internal-skills"  # where _empty_internal_tier points the tier
    skill_dir = _write_skill(internal_dir, "builtin", "a built-in skill")
    (skill_dir / "ref.md").write_text("packaged content\n")
    context = _context(tmp_path, skill_rules=SkillRules(allow=[("internal", "builtin")]))

    result = ReadSkillFileTool(context).apply(
        {"namespace": "internal", "name": "builtin", "path": "ref.md"})
    assert result["content"] == "1|packaged content"
    assert result["namespace"] == "internal"


def test_read_skill_file_rejects_escape(tmp_path: Path) -> None:
    context = _context(tmp_path, skill_rules=SkillRules(allow=[("workspace", "s")]))
    _write_skill(_workspace_skills_dir(context), "s", "d")
    with pytest.raises(ValueError, match="path must"):
        ReadSkillFileTool(context).apply(
            {"namespace": "workspace", "name": "s", "path": "../../secret"})
