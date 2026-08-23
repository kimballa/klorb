# © Copyright 2026 Aaron Kimball
"""Tests for the skill tools: SearchSkills, ActivateSkill, ReadSkillFile."""
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from klorb.permissions.resource import PermissionOverride
from klorb.permissions.skill_access import Namespace, SkillRules
from klorb.permissions.table import PermissionAskRequired
from klorb.process_config import ProcessConfig
from klorb.search_index.catalogs import skill_source_path, skills_catalog_for_namespace
from klorb.search_index.chunk import Chunk
from klorb.session import Session, SessionConfig
from klorb.tools.response_envelope import ToolCallErrorInfo
from klorb.tools.setup_context import ToolSetupContext
from klorb.tools.skill import common as skill_common
from klorb.tools.skill.activate_skill import ActivateSkillTool
from klorb.tools.skill.common import MAX_SKILL_NAME_DISPLAY_LENGTH
from klorb.tools.skill.read_skill_file import ReadSkillFileTool
from klorb.tools.skill.search_skills import SearchSkillsTool
from klorb.workspace import Workspace


@pytest.fixture(autouse=True)
def _empty_internal_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    internal = tmp_path / "internal-skills"
    internal.mkdir()
    monkeypatch.setattr(skill_common, "internal_skills_dir", lambda: internal)
    monkeypatch.setattr(skill_common, "get_klorb_data_dir", lambda: tmp_path / "data")


def _write_skill(base: Path, name: str, description: str, *, body: str = "instructions") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\ndescription: {description}\n---\n\n{body}\n")
    return skill_dir


def _context(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig], *,
        skill_rules: SkillRules | None = None,
        override: PermissionOverride | None = None, claude_skills: bool = False,
) -> ToolSetupContext:
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    process_config = ProcessConfig(compatibility_claude_skills=claude_skills)
    session_config = make_session_config(
        workspace=Workspace(path=ws, trusted=True),
        skill_rules=skill_rules if skill_rules is not None else SkillRules())
    session = Session(session_config, provider=MagicMock(), process_config=process_config)
    return ToolSetupContext(
        process_config=process_config, session_config=session_config, session=session,
        permission_override=override)


def _workspace_skills_dir(context: ToolSetupContext) -> Path:
    return context.session_config.workspace.path / ".klorb" / "skills"


# --- SearchSkills ---


def test_search_matches_name_and_body(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    _write_skill(_workspace_skills_dir(context), "birds", "about birds", body="tweet tweet")
    _write_skill(_workspace_skills_dir(context), "cars", "about cars", body="vroom")

    result = SearchSkillsTool(context).apply({"queries": ["bird"]})
    assert result["match_count"] == 1
    assert result["results"][0]["name"] == "birds"
    assert result["results"][0]["description"] == "about birds"

    # Match against body text.
    body_hit = SearchSkillsTool(context).apply({"queries": ["vroom"]})
    assert [r["name"] for r in body_hit["results"]] == ["cars"]


def test_search_excludes_denied_skill(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config, skill_rules=SkillRules(deny=[("workspace", "birds")]))
    _write_skill(_workspace_skills_dir(context), "birds", "about birds")
    result = SearchSkillsTool(context).apply({"queries": ["bird"]})
    assert result["match_count"] == 0


def test_search_excludes_disable_model_invocation_skill(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(
        tmp_path, make_session_config, skill_rules=SkillRules(allow=[("workspace", "user-only")]))
    skill_dir = _workspace_skills_dir(context) / "user-only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: about birds\ndisable-model-invocation: true\n---\n")
    result = SearchSkillsTool(context).apply({"queries": ["bird"]})
    assert result["match_count"] == 0


def test_search_skills_alias_is_dispatchable(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    assert "SkillSearch" in (SearchSkillsTool(
        _context(tmp_path, make_session_config)).aliases() or ())


def test_search_matches_a_nested_resource_file_not_just_skill_md(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    skill_dir = _write_skill(_workspace_skills_dir(context), "birds", "about birds")
    (skill_dir / "resources").mkdir()
    (skill_dir / "resources" / "notes.md").write_text("a note about migration patterns\n")

    result = SearchSkillsTool(context).apply({"queries": ["migration patterns"]})

    assert [r["name"] for r in result["results"]] == ["birds"]


def test_search_namespace_filter_excludes_other_namespaces(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    _write_skill(_workspace_skills_dir(context), "birds", "about birds")

    result = SearchSkillsTool(context).apply({"queries": ["bird"], "namespace": "internal"})
    assert result["match_count"] == 0

    result = SearchSkillsTool(context).apply({"queries": ["bird"], "namespace": "workspace"})
    assert result["match_count"] == 1


def test_search_namespace_none_or_empty_string_means_all(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    _write_skill(_workspace_skills_dir(context), "birds", "about birds")

    for namespace_arg in (None, ""):
        result = SearchSkillsTool(context).apply({"queries": ["bird"], "namespace": namespace_arg})
        assert result["match_count"] == 1


# --- SearchSkills: semantic hits ---


class _FakeSkillIndexer:
    """A stand-in for `klorb.search_index.indexer.WorkspaceIndexer` that returns a fixed set of
    `(Chunk, score)` hits, filtered to the requested catalog, regardless of query text."""

    def __init__(self, hits: list[tuple[Chunk, float]]) -> None:
        self._hits = hits

    def hybrid_search(self, query_text: str, limit: int, catalog: str) -> list[tuple[Chunk, float]]:
        return [(chunk, score) for chunk, score in self._hits if chunk.catalog == catalog][:limit]


def _semantic_skill_chunk(
    namespace: Namespace, skill_name: str, relative_path: str = "SKILL.md",
) -> Chunk:
    catalog = skills_catalog_for_namespace(namespace)
    source_path = skill_source_path(skill_name, relative_path)
    return Chunk.create(
        catalog=catalog, source_path=source_path, kind="markdown_section",
        start_line=1, end_line=2, text="ignored, only used to identify the skill")


def test_semantic_only_hit_is_included(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    _write_skill(_workspace_skills_dir(context), "birds", "about birds")
    assert context.session is not None
    chunk = _semantic_skill_chunk("workspace", "birds")
    context.session._workspace_indexer = _FakeSkillIndexer(  # type: ignore[assignment]
        [(chunk, 0.5)])

    result = SearchSkillsTool(context).apply({"queries": ["unrelated literal phrase"]})

    assert [r["name"] for r in result["results"]] == ["birds"]
    assert result["results"][0]["namespace"] == "workspace"


def test_semantic_hit_is_not_duplicated_when_already_literally_matched(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    _write_skill(_workspace_skills_dir(context), "birds", "about birds", body="tweet tweet")
    assert context.session is not None
    chunk = _semantic_skill_chunk("workspace", "birds")
    context.session._workspace_indexer = _FakeSkillIndexer(  # type: ignore[assignment]
        [(chunk, 0.5)])

    result = SearchSkillsTool(context).apply({"queries": ["tweet"]})

    assert len(result["results"]) == 1


def test_semantic_hit_for_a_skill_no_longer_discoverable_is_dropped(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A semantic hit for a skill removed, shadowed, or denied since the index was last scanned
    is silently dropped -- the index isn't necessarily as fresh as the live skill catalog."""
    context = _context(tmp_path, make_session_config)  # no "birds" skill written to disk
    assert context.session is not None
    chunk = _semantic_skill_chunk("workspace", "birds")
    context.session._workspace_indexer = _FakeSkillIndexer(  # type: ignore[assignment]
        [(chunk, 0.5)])

    result = SearchSkillsTool(context).apply({"queries": ["unrelated literal phrase"]})

    assert result["results"] == []


def test_semantic_hits_respect_the_namespace_filter(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    _write_skill(_workspace_skills_dir(context), "birds", "about birds")
    assert context.session is not None
    chunk = _semantic_skill_chunk("workspace", "birds")
    context.session._workspace_indexer = _FakeSkillIndexer(  # type: ignore[assignment]
        [(chunk, 0.5)])

    result = SearchSkillsTool(context).apply(
        {"queries": ["unrelated literal phrase"], "namespace": "internal"})

    assert result["results"] == []


# --- ActivateSkill ---


def test_activate_allow_returns_content_and_manifest(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config, skill_rules=SkillRules(allow=[("workspace", "s")]))
    skill_dir = _write_skill(_workspace_skills_dir(context), "s", "do the thing", body="step one")
    (skill_dir / "ref.md").write_text("reference")

    result = ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})
    assert result["namespace"] == "workspace"
    assert result["name"] == "s"
    assert "step one" in result["content"]
    assert result["files"] == ["SKILL.md", "ref.md"]
    assert isinstance(result["tokens"], int)
    assert result["tokens"] > 0


def test_activate_grants_metadata_klorb_hooks_into_the_session(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config, skill_rules=SkillRules(allow=[("workspace", "s")]))
    skill_dir = _workspace_skills_dir(context) / "s"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "description: do the thing\n"
        "metadata:\n"
        "  klorb:\n"
        "    hooks:\n"
        "      onToolUse:\n"
        "        - type: chat\n"
        "          prompt: granted by skill\n"
        "---\n\nstep one\n")

    ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})

    assert context.session is not None
    assert [h.prompt for h in context.session.config.hooks["onToolUse"]] == ["granted by skill"]
    # Not heritable to a subagent -- metadata.klorb.hooks defaults isHeritable to False.
    assert context.session.config.hooks["onToolUse"][0].is_heritable is False


def test_activate_pre_denied_skill_raises_value_error_not_found(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A skill already `"deny"`-verdicted when the catalog is first built is excluded from the
    catalog entirely (see docs/specs/skills.md), so activating it reads the same as an unknown
    name -- there's no permission question to raise, since the skill was never resolvable."""
    context = _context(tmp_path, make_session_config, skill_rules=SkillRules(deny=[("workspace", "s")]))
    _write_skill(_workspace_skills_dir(context), "s", "d")
    with pytest.raises(ValueError, match="no such skill"):
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})


def test_activate_deny_after_catalog_built_raises_permission_error(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A skill denied *after* the catalog was already built (e.g. an interactive ask answered
    "deny" mid-session) stays resolvable in memory until an explicit reload, so its `skillRules`
    verdict is still checked at every use -- this path still raises `PermissionError`, unlike the
    pre-denied case above."""
    context = _context(tmp_path, make_session_config)  # empty rules -> ask
    _write_skill(_workspace_skills_dir(context), "s", "d")
    assert context.session is not None
    # Force the catalog to build while the skill is merely "ask"-verdicted.
    context.session.skill_catalog_registry.ensure_from_context(context)
    assert context.session.skill_catalog_registry.canonical().get(("workspace", "s")) is not None

    context.session_config.skill_rules = SkillRules(deny=[("workspace", "s")])
    with pytest.raises(PermissionError):
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})


def test_activate_ask_raises_permission_ask_with_skill(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)  # empty rules -> ask
    _write_skill(_workspace_skills_dir(context), "s", "the description")
    with pytest.raises(PermissionAskRequired) as exc:
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})
    assert exc.value.skill == ("workspace", "s")
    assert "the description" in str(exc.value)


def test_activate_once_override_bypasses_ask(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    override = PermissionOverride(skills=frozenset({("workspace", "s")}))
    context = _context(tmp_path, make_session_config, override=override)
    _write_skill(_workspace_skills_dir(context), "s", "d", body="loaded")
    result = ActivateSkillTool(context).apply({"namespace": "workspace", "name": "s"})
    assert "loaded" in result["content"]


def test_activate_unknown_skill_raises_value_error(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    with pytest.raises(ValueError, match="no such skill"):
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "ghost"})


def test_activate_bad_name_raises_value_error(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config)
    with pytest.raises(ValueError, match="skill name"):
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "../escape"})


def test_activate_over_long_name_resolves_via_truncated_identity(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    """A skill directory whose basename is longer than `MAX_SKILL_NAME_DISPLAY_LENGTH` is
    catalogued under its truncated form -- the same name every advertised listing shows -- so
    calling `ActivateSkill` with exactly that (truncated) name always resolves, even though the
    real directory name on disk is longer."""
    long_name = "b" * (MAX_SKILL_NAME_DISPLAY_LENGTH + 15)
    truncated_name = long_name[:MAX_SKILL_NAME_DISPLAY_LENGTH]
    context = _context(
        tmp_path, make_session_config, skill_rules=SkillRules(allow=[("workspace", truncated_name)]))
    _write_skill(_workspace_skills_dir(context), long_name, "d", body="the steps")

    result = ActivateSkillTool(context).apply({"namespace": "workspace", "name": truncated_name})

    assert result["name"] == truncated_name
    assert "the steps" in result["content"]
    # The untruncated real name is not a resolvable identity for ActivateSkill.
    with pytest.raises(ValueError, match="no such skill"):
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": long_name})


def test_activate_disable_model_invocation_skill_refuses_with_tailored_message(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    """The one legitimate way into a `disable-model-invocation` skill is the user's own message
    starting with `/<name>` (`UserSkillActivation`) -- a model that guesses the name and calls
    ActivateSkill directly must get a specific, actionable refusal, not the generic "no such
    skill" a genuinely-unknown name gets."""
    context = _context(
        tmp_path, make_session_config, skill_rules=SkillRules(allow=[("workspace", "user-only")]))
    skill_dir = _workspace_skills_dir(context) / "user-only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: d\ndisable-model-invocation: true\n---\n\nsecret steps\n")

    with pytest.raises(ValueError, match="/user-only"):
        ActivateSkillTool(context).apply({"namespace": "workspace", "name": "user-only"})


# --- ReadSkillFile ---


def test_read_skill_file_allow(tmp_path: Path, make_session_config: Callable[..., SessionConfig]) -> None:
    context = _context(tmp_path, make_session_config, skill_rules=SkillRules(allow=[("workspace", "s")]))
    skill_dir = _write_skill(_workspace_skills_dir(context), "s", "d")
    (skill_dir / "ref.md").write_text("line1\nline2\nline3\n")

    result = ReadSkillFileTool(context).apply(
        {"namespace": "workspace", "name": "s", "path": "ref.md"})
    assert result["content"] == "1|line1\n2|line2\n3|line3"
    assert result["namespace"] == "workspace"
    assert result["name"] == "s"
    assert result["path"] == "ref.md"


def test_read_skill_file_update_args_drops_everything_on_success(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig],
) -> None:
    context = _context(tmp_path, make_session_config, skill_rules=SkillRules(allow=[("workspace", "s")]))
    skill_dir = _write_skill(_workspace_skills_dir(context), "s", "d")
    (skill_dir / "ref.md").write_text("line1\n")
    args = {"namespace": "workspace", "name": "s", "path": "ref.md"}

    updated = ReadSkillFileTool(context).update_args(
        args, None, ToolCallErrorInfo(is_error=False, is_retryable=False))

    assert updated == {}


def test_read_skill_file_gated_by_verdict(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    # An un-activated ask-verdict skill raises the activation ask on ReadSkillFile too.
    context = _context(tmp_path, make_session_config)
    skill_dir = _write_skill(_workspace_skills_dir(context), "s", "d")
    (skill_dir / "ref.md").write_text("x")
    with pytest.raises(PermissionAskRequired) as exc:
        ReadSkillFileTool(context).apply({"namespace": "workspace", "name": "s", "path": "ref.md"})
    assert exc.value.skill == ("workspace", "s")


def test_read_internal_skill_file_via_resource_loader(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    # An internal-tier skill's supporting file is read through the resource loader
    # (ReadFileCore.apply_readable), not the builtin open() — so it works even for a
    # zip/wheel-installed klorb whose packaged resources aren't plain filesystem paths.
    internal_dir = tmp_path / "internal-skills"  # where _empty_internal_tier points the tier
    skill_dir = _write_skill(internal_dir, "builtin", "a built-in skill")
    (skill_dir / "ref.md").write_text("packaged content\n")
    context = _context(tmp_path, make_session_config, skill_rules=SkillRules(allow=[("internal", "builtin")]))

    result = ReadSkillFileTool(context).apply(
        {"namespace": "internal", "name": "builtin", "path": "ref.md"})
    assert result["content"] == "1|packaged content"
    assert result["namespace"] == "internal"


def test_read_skill_file_rejects_escape(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config, skill_rules=SkillRules(allow=[("workspace", "s")]))
    _write_skill(_workspace_skills_dir(context), "s", "d")
    with pytest.raises(ValueError, match="path must"):
        ReadSkillFileTool(context).apply(
            {"namespace": "workspace", "name": "s", "path": "../../secret"})


def test_read_skill_file_preview_open_full_rereads_the_whole_file(
    tmp_path: Path, make_session_config: Callable[..., SessionConfig]
) -> None:
    context = _context(tmp_path, make_session_config, skill_rules=SkillRules(allow=[("workspace", "s")]))
    skill_dir = _write_skill(_workspace_skills_dir(context), "s", "d")
    (skill_dir / "ref.md").write_text("line1\nline2\nline3\n")
    tool = ReadSkillFileTool(context)
    args = {"namespace": "workspace", "name": "s", "path": "ref.md"}

    result = tool.apply(args)
    preview = tool.read_preview(args, result)

    assert preview is not None
    assert preview.preview_lines == [(1, "line1"), (2, "line2"), (3, "line3")]

    full_view = preview.open_full()
    assert full_view.error is None
    assert full_view.lines == [(1, "line1"), (2, "line2"), (3, "line3")]
