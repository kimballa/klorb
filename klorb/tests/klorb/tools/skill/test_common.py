# © Copyright 2026 Aaron Kimball
"""Tests for klorb.tools.skill.common: discovery, resolution, frontmatter parsing, name/path
validation, the manifest, and the skillRules gate."""

from pathlib import Path

import pytest

from klorb.permissions.resource import PermissionOverride
from klorb.permissions.skill_access import SkillRules
from klorb.permissions.table import PermissionAskRequired
from klorb.tools.skill import common as skill_common
from klorb.tools.skill.catalog import SkillCatalog, build_catalogs, resolve_and_gate_skill
from klorb.tools.skill.common import (
    is_valid_skill_name,
    parse_frontmatter,
    raise_if_skill_not_allowed,
    resolve_all_skills,
    resolve_skill_file,
    skill_file_manifest,
    validate_namespace,
    validate_skill_name,
)


def _write_skill(base: Path, name: str, description: str = "", *, body: str = "body text") -> Path:
    """Create a skill directory `base/<name>/` with a SKILL.md carrying `description`."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = f"---\ndescription: {description}\n---\n\n{body}\n" if description else f"{body}\n"
    (skill_dir / "SKILL.md").write_text(frontmatter)
    return skill_dir


@pytest.fixture(autouse=True)
def _empty_internal_tier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the internal tier at an empty temp dir so the packaged create-edit-skill doesn't
    appear in these tests' discovery results. Tests that want the internal tier populate it."""
    internal = tmp_path / "internal-skills"
    internal.mkdir()
    monkeypatch.setattr(skill_common, "internal_skills_dir", lambda: internal)
    monkeypatch.setattr(skill_common, "KLORB_DATA_DIR", tmp_path / "data")


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    return ws


# --- name / namespace validation ---


def test_validate_namespace() -> None:
    assert validate_namespace("workspace") == "workspace"
    assert validate_namespace("user") == "user"
    assert validate_namespace("internal") == "internal"
    with pytest.raises(ValueError, match="namespace must be one of"):
        validate_namespace("bogus")


def test_validate_skill_name_rejects_separators_and_traversal() -> None:
    assert validate_skill_name("add-cli-flag") == "add-cli-flag"
    for bad in ["", "..", ".", "a/b", "a\\b", "../x", "internal:foo"]:
        with pytest.raises(ValueError, match="skill name"):
            validate_skill_name(bad)


def test_is_valid_skill_name() -> None:
    assert is_valid_skill_name("foo-bar")
    assert not is_valid_skill_name("")
    assert not is_valid_skill_name("..")
    assert not is_valid_skill_name("a/b")
    assert not is_valid_skill_name("a:b")


# --- frontmatter parsing ---


def _write_raw_skill(base: Path, name: str, text: str) -> None:
    """Create a skill directory `base/<name>/SKILL.md` with `text` verbatim, for exercising
    frontmatter parsing edge cases the `_write_skill` helper's `description` shortcut can't."""
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text)


def test_skill_description_folded_scalar(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    text = "---\ndescription: >\n  line one\n  line two\n---\n\nbody"
    _write_raw_skill(ws / ".klorb" / "skills", "s", text)
    catalog = _catalog(tmp_path, ws)
    skill = catalog.get(("workspace", "s"))
    assert skill is not None
    assert skill.description == "line one line two"


def test_skill_description_missing_or_malformed_yields_empty(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    cases = {
        "no-frontmatter": "no frontmatter here",
        "unclosed": "---\nnot closed\n",
        "bad-yaml": "---\n: : bad yaml :\n---\n",
        "no-description": "---\nother: 1\n---\n",
        # Non-string description (a list) is treated as empty.
        "list-description": "---\ndescription: [a, b]\n---\n",
    }
    for name, text in cases.items():
        _write_raw_skill(ws / ".klorb" / "skills", name, text)
    catalog = _catalog(tmp_path, ws)
    for name in cases:
        skill = catalog.get(("workspace", name))
        assert skill is not None
        assert skill.description == ""


def test_safe_load_refuses_python_object_tags(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    # A hostile !!python/object tag must not construct anything; it parses to empty description.
    text = "---\ndescription: !!python/object/apply:os.system ['echo hi']\n---\n"
    _write_raw_skill(ws / ".klorb" / "skills", "s", text)
    catalog = _catalog(tmp_path, ws)
    skill = catalog.get(("workspace", "s"))
    assert skill is not None
    assert skill.description == ""


def test_parse_frontmatter_returns_raw_dict() -> None:
    text = "---\nname: my-skill\ndescription: does the thing\n---\n\nbody"
    assert parse_frontmatter(text) == {"name": "my-skill", "description": "does the thing"}


def test_parse_frontmatter_missing_or_malformed_yields_empty_dict() -> None:
    assert parse_frontmatter("no frontmatter here") == {}
    assert parse_frontmatter("---\nnot closed\n") == {}
    assert parse_frontmatter("---\n: : bad yaml :\n---\n") == {}
    assert parse_frontmatter("---\n- a\n- b\n---\n") == {}


# --- discovery / precedence ---


def test_discover_lists_non_denied_skills_sorted(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_skill(ws / ".klorb" / "skills", "zebra", "z skill")
    _write_skill(ws / ".klorb" / "skills", "alpha", "a skill")
    found = _catalog(tmp_path, ws).discoverable(SkillRules())
    assert [(s.name, s.namespace, s.description) for s in found] == [
        ("alpha", "workspace", "a skill"),
        ("zebra", "workspace", "z skill"),
    ]


def test_discovery_skips_dir_without_skill_md(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    (ws / ".klorb" / "skills" / "no-md").mkdir(parents=True)
    _write_skill(ws / ".klorb" / "skills", "has-md")
    found = _catalog(tmp_path, ws).discoverable(SkillRules())
    assert [s.name for s in found] == ["has-md"]


def test_untrusted_workspace_contributes_no_skills(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_skill(ws / ".klorb" / "skills", "secret", "should not appear")
    catalog = _catalog(tmp_path, ws, workspace_trusted=False)
    assert catalog.discoverable(SkillRules()) == []
    assert catalog.get(("workspace", "secret")) is None


def test_denied_skill_excluded_from_discovery(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_skill(ws / ".klorb" / "skills", "shown")
    _write_skill(ws / ".klorb" / "skills", "hidden")
    rules = SkillRules(deny=[("workspace", "hidden")])
    found = _catalog(tmp_path, ws).discoverable(rules)
    assert [s.name for s in found] == ["shown"]


def test_user_shadows_workspace_and_internal(tmp_path: Path) -> None:
    # Precedence order is user (homedir), then workspace (project), then internal (packaged) --
    # a homedir skill overrides a same-named project skill.
    ws = _workspace(tmp_path)
    _write_skill(ws / ".klorb" / "skills", "dup", "workspace copy")
    _write_skill(tmp_path / "data" / "skills", "dup", "user copy")
    resolved = resolve_all_skills(
        workspace_root=ws, workspace_trusted=True, claude_skills_compat=False)
    dup = [r for r in resolved if r.name == "dup"]
    assert len(dup) == 1
    assert dup[0].namespace == "user"


def test_claude_skills_compat_adds_workspace_source(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_skill(ws / ".claude" / "skills", "claude-only", "from .claude")
    # Disabled: not discovered.
    off = _catalog(tmp_path, ws, claude_skills_compat=False).discoverable(SkillRules())
    assert [s.name for s in off] == []
    # Enabled: discovered as a workspace-namespace skill.
    on = _catalog(tmp_path, ws, claude_skills_compat=True).discoverable(SkillRules())
    assert [(s.name, s.namespace) for s in on] == [("claude-only", "workspace")]


def test_klorb_skills_wins_over_claude_skills_on_collision(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_skill(ws / ".klorb" / "skills", "dup", "klorb copy")
    _write_skill(ws / ".claude" / "skills", "dup", "claude copy")
    found = _catalog(tmp_path, ws, claude_skills_compat=True).discoverable(SkillRules())
    assert [(s.name, s.description) for s in found] == [("dup", "klorb copy")]


# --- resolve / manifest / file reads ---


def test_resolve_skill_exact_namespace(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_skill(tmp_path / "data" / "skills", "u", "user skill")
    catalog = _catalog(tmp_path, ws)
    resolved = catalog.get(("user", "u"))
    assert resolved is not None
    assert resolved.namespace == "user"
    # Asking for it under the wrong namespace finds nothing.
    assert catalog.get(("workspace", "u")) is None


def test_manifest_is_sorted_relative_paths(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    skill_dir = _write_skill(ws / ".klorb" / "skills", "s")
    (skill_dir / "reference").mkdir()
    (skill_dir / "reference" / "b.md").write_text("b")
    (skill_dir / "a.txt").write_text("a")
    resolved = _catalog(tmp_path, ws).get(("workspace", "s"))
    assert resolved is not None
    assert skill_file_manifest(resolved) == ["SKILL.md", "a.txt", "reference/b.md"]


def test_manifest_excludes_symlink_escape(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    skill_dir = _write_skill(ws / ".klorb" / "skills", "s")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (skill_dir / "link.txt").symlink_to(outside)
    resolved = _catalog(tmp_path, ws).get(("workspace", "s"))
    assert resolved is not None
    assert skill_file_manifest(resolved) == ["SKILL.md"]


def test_resolve_skill_file_reads_supporting_file(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    skill_dir = _write_skill(ws / ".klorb" / "skills", "s")
    (skill_dir / "ref.md").write_text("hello")
    resolved = _catalog(tmp_path, ws).get(("workspace", "s"))
    assert resolved is not None
    path = resolve_skill_file(resolved, "ref.md")
    assert path.read_text() == "hello"


def test_resolve_skill_file_rejects_escapes(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_skill(ws / ".klorb" / "skills", "s")
    (tmp_path / "secret.txt").write_text("secret")
    resolved = _catalog(tmp_path, ws).get(("workspace", "s"))
    assert resolved is not None
    for bad in ["../secret.txt", "/etc/passwd", "~/secret", "a/../../secret.txt"]:
        with pytest.raises(ValueError, match="path must"):
            resolve_skill_file(resolved, bad)
    with pytest.raises(FileNotFoundError):
        resolve_skill_file(resolved, "nope.md")


def test_resolve_skill_file_symlink_escape_blocked(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    skill_dir = _write_skill(ws / ".klorb" / "skills", "s")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (skill_dir / "link.txt").symlink_to(outside)
    resolved = _catalog(tmp_path, ws).get(("workspace", "s"))
    assert resolved is not None
    with pytest.raises(ValueError, match="escapes the skill directory"):
        resolve_skill_file(resolved, "link.txt")


# --- gate ---


def test_raise_if_skill_not_allowed_verdicts() -> None:
    # allow: returns
    raise_if_skill_not_allowed(
        SkillRules(allow=[("internal", "s")]), None, "internal", "s", description="d")
    # deny: PermissionError
    with pytest.raises(PermissionError):
        raise_if_skill_not_allowed(
            SkillRules(deny=[("internal", "s")]), None, "internal", "s", description="d")
    # ask (unmatched): PermissionAskRequired carrying the skill identity
    with pytest.raises(PermissionAskRequired) as exc:
        raise_if_skill_not_allowed(SkillRules(), None, "internal", "s", description="d")
    assert exc.value.skill == ("internal", "s")


def test_once_override_bypasses_ask() -> None:
    override = PermissionOverride(skills=frozenset({("internal", "s")}))
    # No exception raised despite ask verdict, because the override covers it.
    raise_if_skill_not_allowed(SkillRules(), override, "internal", "s", description="d")


def test_override_never_bypasses_deny() -> None:
    override = PermissionOverride(skills=frozenset({("internal", "s")}))
    with pytest.raises(PermissionError):
        raise_if_skill_not_allowed(
            SkillRules(deny=[("internal", "s")]), override, "internal", "s", description="d")


def _catalog(
    tmp_path: Path, ws: Path, *, workspace_trusted: bool = True, claude_skills_compat: bool = False,
) -> SkillCatalog:
    return build_catalogs(
        workspace_root=ws, workspace_trusted=workspace_trusted,
        claude_skills_compat=claude_skills_compat).canonical


def test_resolve_and_gate_unknown_skill_raises_value_error(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    with pytest.raises(ValueError, match="no such skill"):
        resolve_and_gate_skill(
            catalog=_catalog(tmp_path, ws), skill_rules=SkillRules(), override=None,
            namespace="workspace", name="ghost")


def test_resolve_and_gate_finds_catalog_skill(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    _write_skill(ws / ".klorb" / "skills", "s", "does the thing")
    skill = resolve_and_gate_skill(
        catalog=_catalog(tmp_path, ws), skill_rules=SkillRules(allow=[("workspace", "s")]),
        override=None, namespace="workspace", name="s")
    assert skill.namespace == "workspace"
    assert skill.name == "s"
    assert skill.description == "does the thing"
