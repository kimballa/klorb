# © Copyright 2026 Aaron Kimball
"""Session-level skill behavior: the AvailableSkills / SkillReference interjections and the
ActivateSkill permission-ask -> grant -> retry flow through Session._run_tool_calls."""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.api_provider import ProviderResponse
from klorb.message import Message, ToolCallRequest
from klorb.permissions.skill_access import SkillRules
from klorb.process_config import ProcessConfig
from klorb.session import PermissionAskContext, PermissionDecision, Session, SessionConfig, TurnEventHandlers
from klorb.tools.registry import ToolRegistry
from klorb.tools.skill import common as skill_common
from klorb.tools.skill.common import discover_skills as real_discover_skills
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _real_discovery_with_empty_internal_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Restore the real `discover_skills` in `klorb.session` (the suite-wide conftest fixture
    neutralizes it) but point the internal tier at an empty dir, so only skills these tests write
    into the workspace tier are discovered."""
    internal = tmp_path / "internal-skills"
    internal.mkdir()
    monkeypatch.setattr(skill_common, "internal_skills_dir", lambda: internal)
    monkeypatch.setattr(skill_common, "KLORB_DATA_DIR", tmp_path / "data")
    monkeypatch.setattr("klorb.session.discover_skills", real_discover_skills)


def _write_skill(base: Path, name: str, description: str, *, body: str = "instructions") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\ndescription: {description}\n---\n\n{body}\n")
    return skill_dir


def _reply(content: str = "ok") -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content=content, role="assistant", num_tokens=0,
            processing_state="complete", timestamp=datetime.now(), finish_reason="stop"),
        prompt_tokens=0)


def _tool_call_reply(call_id: str, name: str, arguments: str) -> ProviderResponse:
    return ProviderResponse(
        message=Message(
            content="", role="assistant", num_tokens=0, processing_state="complete",
            timestamp=datetime.now(), finish_reason="tool_calls",
            tool_calls=[ToolCallRequest(id=call_id, name=name, arguments=arguments)]),
        prompt_tokens=0)


def _session(
    tmp_path: Path, *, trusted: bool = True, claude_skills: bool = False,
    skill_rules: SkillRules | None = None, provider: MagicMock | None = None,
) -> Session:
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    config = SessionConfig(
        model="some/model", workspace=Workspace(path=ws, trusted=trusted),
        skill_rules=skill_rules if skill_rules is not None else SkillRules())
    return Session(
        config,
        provider=provider if provider is not None else MagicMock(),
        process_config=ProcessConfig(compatibility_claude_skills=claude_skills))


def _workspace_skills(session: Session) -> Path:
    return session.config.workspace.path / ".klorb" / "skills"


def _user_content(session: Session) -> str:
    return [m for m in session.messages if m.role == "user"][0].content


# --- AvailableSkills interjection ---


def test_available_skills_interjection_listed_on_first_turn(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")

    session.send_turn("hello")
    content = _user_content(session)
    assert '<SystemInterjection subject="AvailableSkills">' in content
    assert "- do-thing (workspace): does the thing" in content
    assert "hello" in content


def test_available_skills_interjection_only_once(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")

    session.send_turn("first")
    session.send_turn("second")
    contents = [m.content for m in session.messages if m.role == "user"]
    assert "AvailableSkills" in contents[0]
    assert "AvailableSkills" not in contents[1]


def test_no_available_skills_interjection_when_none_discoverable(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)  # no skills written
    session.send_turn("hello")
    assert "AvailableSkills" not in _user_content(session)


def test_denied_skill_absent_from_available_list(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(
        tmp_path, skill_rules=SkillRules(deny=[("workspace", "secret")]), provider=provider)
    _write_skill(_workspace_skills(session), "secret", "hidden")
    session.send_turn("hello")
    assert "AvailableSkills" not in _user_content(session)


def test_claude_skills_compat_listed_when_enabled(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, claude_skills=True, provider=provider)
    _write_skill(
        session.config.workspace.path / ".claude" / "skills", "claude-skill", "from claude")
    session.send_turn("hello")
    assert "- claude-skill (workspace): from claude" in _user_content(session)


# --- SkillReference interjection ---


def test_skill_reference_interjection_on_slash_mention(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")

    session.send_turn("please run /do-thing now")
    content = _user_content(session)
    assert '<SystemInterjection subject="SkillReference">' in content


def test_no_skill_reference_for_unknown_slash_token(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")

    session.send_turn("look at src/main.py and /usr/bin")
    assert "SkillReference" not in _user_content(session)


def test_skill_reference_fires_each_turn_mentioned(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")

    session.send_turn("use /do-thing")
    session.send_turn("use /do-thing again")
    contents = [m.content for m in session.messages if m.role == "user"]
    assert "SkillReference" in contents[0]
    assert "SkillReference" in contents[1]


# --- Activation ask -> grant -> retry flow ---


def test_activate_skill_ask_flow_grants_and_activates(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.side_effect = [
        _tool_call_reply("c1", "ActivateSkill", '{"namespace": "workspace", "name": "do-thing"}'),
        _reply("done"),
    ]
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing", body="the steps")
    session._tool_registry = ToolRegistry(ProcessConfig(), session.config)
    session._tool_registry.session = session

    asked: list[PermissionAskContext] = []

    def on_ask(ctx: PermissionAskContext) -> PermissionDecision:
        asked.append(ctx)
        return PermissionDecision(action="allow", scope="session")

    session.send_turn("go", TurnEventHandlers(on_permission_ask=on_ask))

    # The ask carried the skill identity...
    assert len(asked) == 1
    assert asked[0].skill == ("workspace", "do-thing")
    # ...the session-scope grant was recorded...
    assert ("workspace", "do-thing") in session.config.skill_rules.allow
    # ...and the retried activation returned the skill content as a tool_response.
    tool_responses = [m for m in session.messages if m.role == "tool_response"]
    assert any("the steps" in m.content for m in tool_responses)


def test_activate_skill_ask_denied(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.side_effect = [
        _tool_call_reply("c1", "ActivateSkill", '{"namespace": "workspace", "name": "do-thing"}'),
        _reply("done"),
    ]
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")
    session._tool_registry = ToolRegistry(ProcessConfig(), session.config)
    session._tool_registry.session = session

    def on_ask(ctx: PermissionAskContext) -> PermissionDecision:
        return PermissionDecision(action="deny", scope="once")

    session.send_turn("go", TurnEventHandlers(on_permission_ask=on_ask))
    assert ("workspace", "do-thing") not in session.config.skill_rules.allow
    tool_responses = [m for m in session.messages if m.role == "tool_response"]
    assert any("Permission denied" in m.content for m in tool_responses)
