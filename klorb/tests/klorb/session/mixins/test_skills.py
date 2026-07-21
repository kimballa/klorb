# © Copyright 2026 Aaron Kimball
"""Session-level skill behavior: the AvailableSkills / SkillReference / UserSkillActivation
interjections, the process-wide skill catalog, and the ActivateSkill permission-ask -> grant ->
retry flow through Session._run_tool_calls."""

import json
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
from klorb.tools.skill import catalog as skill_catalog
from klorb.workspace import Workspace


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


def test_skill_reference_resolves_colon_qualified_fqsn(tmp_path: Path) -> None:
    """A mention like `/workspace:do-thing` resolves that exact (namespace, name) pair, not just
    a bare-name search -- see docs/specs/skills.md."""
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")

    session.send_turn("not first: please use /workspace:do-thing here")
    content = _user_content(session)
    assert '<SystemInterjection subject="SkillReference">' in content
    assert "- do-thing (workspace): does the thing" in content


# --- UserSkillActivation (a prompt that starts with a skill reference) ---


def test_leading_skill_reference_activates_unconditionally(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(
        tmp_path, skill_rules=SkillRules(allow=[("workspace", "do-thing")]), provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing", body="the exact steps")

    session.send_turn("/do-thing please handle this now")
    content = _user_content(session)
    assert '<SystemInterjection subject="UserSkillActivation">' in content
    assert "the exact steps" in content
    assert "please handle this now" in content
    # It's excluded from the more casual SkillReference reminder, since it already got the full
    # activation treatment.
    assert "SkillReference" not in content


def test_leading_skill_reference_not_activated_without_allow(tmp_path: Path) -> None:
    """An "ask"-verdicted skill never gets its content auto-injected -- only the ordinary
    SkillReference reminder, so the model still has to call ActivateSkill and go through the
    normal approval flow."""
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)  # empty rules -> ask
    _write_skill(_workspace_skills(session), "do-thing", "does the thing", body="secret steps")

    session.send_turn("/do-thing please handle this now")
    content = _user_content(session)
    assert "UserSkillActivation" not in content
    assert "secret steps" not in content
    assert '<SystemInterjection subject="SkillReference">' in content


def test_leading_skill_reference_denied_gets_no_special_treatment(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(
        tmp_path, skill_rules=SkillRules(deny=[("workspace", "do-thing")]), provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")

    session.send_turn("/do-thing please handle this now")
    content = _user_content(session)
    assert "UserSkillActivation" not in content
    assert "SkillReference" not in content


def test_other_mention_alongside_leading_activation_still_referenced(tmp_path: Path) -> None:
    """"/skill-1 bla bla /skill-2": skill-1 (leading) is unconditionally activated; skill-2,
    mentioned elsewhere in the same message, still gets an ordinary SkillReference reminder."""
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(
        tmp_path,
        skill_rules=SkillRules(allow=[("workspace", "skill-1"), ("workspace", "skill-2")]),
        provider=provider)
    _write_skill(_workspace_skills(session), "skill-1", "the first skill", body="steps one")
    _write_skill(_workspace_skills(session), "skill-2", "the second skill", body="steps two")

    session.send_turn("/skill-1 bla bla /skill-2")
    content = _user_content(session)
    assert '<SystemInterjection subject="UserSkillActivation">' in content
    assert "steps one" in content
    assert '<SystemInterjection subject="SkillReference">' in content
    reference_block = content.split('<SystemInterjection subject="SkillReference">', 1)[1].split(
        "</SystemInterjection>", 1)[0]
    assert "- skill-2 (workspace): the second skill" in reference_block
    # skill-1 shouldn't also show up in the casual reminder list (it's excluded because the
    # leading-mention activation already fully handled it) -- even though it's still listed in
    # the separate, unrelated AvailableSkills interjection.
    assert "skill-1" not in reference_block


def test_leading_skill_activation_uses_canonical_name_in_message(tmp_path: Path) -> None:
    """A user may type a skill by its frontmatter-alias name; the interjection still names it by
    its canonical (directory-basename) identity."""
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(
        tmp_path, skill_rules=SkillRules(allow=[("workspace", "do-thing")]), provider=provider)
    skill_dir = _workspace_skills(session) / "do-thing"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alias-name\ndescription: does the thing\n---\n\nthe steps\n")

    session.send_turn("/alias-name go")
    content = _user_content(session)
    assert '<SystemInterjection subject="UserSkillActivation">' in content
    assert "invoked skill do-thing" in content
    payload = json.loads(content.split("apply this skill:\n", 1)[1].split("\n</SystemInterjection>")[0])
    assert payload["name"] == "do-thing"


# --- Process-wide catalog: built once, not per turn ---


def test_catalog_built_once_even_with_a_skill_mention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turn 1 needs the AvailableSkills list, the SkillReference reminder, and (for a leading
    mention) the UserSkillActivation check -- all three must share one process-wide catalog
    rather than each re-scanning the tiers."""
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")

    spy = MagicMock(side_effect=skill_catalog.build_catalogs)
    monkeypatch.setattr(skill_catalog, "build_catalogs", spy)
    session.send_turn("please run /do-thing now")

    assert spy.call_count == 1
    content = _user_content(session)
    assert "AvailableSkills" in content
    assert "SkillReference" in content


def test_catalog_not_rebuilt_on_a_later_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the process-wide catalog is built on turn 1, a later turn -- mention or not -- must
    not trigger another disk scan."""
    provider = MagicMock()
    provider.send_prompt.return_value = _reply()
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing")
    session.send_turn("first turn, builds the catalog")

    spy = MagicMock(side_effect=skill_catalog.build_catalogs)
    monkeypatch.setattr(skill_catalog, "build_catalogs", spy)
    session.send_turn("second turn, mentions /do-thing again")

    spy.assert_not_called()


# --- Activation ask -> grant -> retry flow ---


def test_activate_skill_ask_flow_grants_and_activates(tmp_path: Path) -> None:
    provider = MagicMock()
    provider.send_prompt.side_effect = [
        _tool_call_reply("c1", "ActivateSkill", '{"namespace": "workspace", "name": "do-thing"}'),
        _reply("done"),
    ]
    session = _session(tmp_path, provider=provider)
    _write_skill(_workspace_skills(session), "do-thing", "does the thing", body="the steps")
    session._tool_registry = ToolRegistry.discover_tools(ProcessConfig(), session.config)
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
    session._tool_registry = ToolRegistry.discover_tools(ProcessConfig(), session.config)
    session._tool_registry.session = session

    def on_ask(ctx: PermissionAskContext) -> PermissionDecision:
        return PermissionDecision(action="deny", scope="once")

    session.send_turn("go", TurnEventHandlers(on_permission_ask=on_ask))
    assert ("workspace", "do-thing") not in session.config.skill_rules.allow
    tool_responses = [m for m in session.messages if m.role == "tool_response"]
    assert any("Permission denied" in m.content for m in tool_responses)
