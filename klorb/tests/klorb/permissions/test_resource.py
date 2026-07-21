# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions.resource: the PermissionResource hierarchy (PathResource/
CommandResource/SkillResource/DomainResource/StructuralResource) that replaces the flat
path/command/skill/url optional-field bag on PermissionAskItem/PermissionAskContext. See
docs/plans/archive/014-permission-resource-hierarchy.md.
"""

from pathlib import Path

from klorb.permissions.command_access import CommandRules
from klorb.permissions.resource import (
    CommandResource,
    DomainResource,
    GrantPreview,
    PathResource,
    PermissionOverride,
    SkillResource,
    StructuralResource,
)
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.workspace import Workspace

# --- PathResource ---


def test_path_resource_header_kind_names_read_or_write() -> None:
    assert PathResource(path=Path("/f.txt"), is_write=True).header_kind() == "Write file"
    assert PathResource(path=Path("/f.txt"), is_write=False).header_kind() == "Read file"


def test_path_resource_preview_text_is_the_path() -> None:
    assert PathResource(path=Path("/a/b.txt")).preview_text() == "/a/b.txt"


def test_path_resource_is_persistable() -> None:
    assert PathResource(path=Path("/f.txt")).is_persistable is True


def test_path_resource_grant_preview_falls_back_to_containing_directory(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    target = tmp_path / "f.txt"

    preview = PathResource(path=target, is_write=True).grant_preview(session_config)

    assert preview == GrantPreview(resource_text=str(tmp_path), block=True)


def test_path_resource_apply_grant_persists_to_session_config(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    target = tmp_path / "f.txt"

    PathResource(path=target, is_write=True).apply_grant(
        "allow", "session", session_config, None)

    assert session_config.write_dirs.allow == [tmp_path]
    assert session_config.read_dirs.allow == [tmp_path]


def test_path_resource_added_to_override_adds_only_its_own_field() -> None:
    resource = PathResource(path=Path("/f.txt"))

    override = resource.added_to_override(PermissionOverride())

    assert override.paths == frozenset({Path("/f.txt")})
    assert override.commands == frozenset()
    assert override.skills == frozenset()
    assert override.domains == frozenset()
    assert override.reasons == frozenset()


# --- CommandResource ---


def test_command_resource_header_kind_and_preview_text() -> None:
    resource = CommandResource(argv=("git", "status"))
    assert resource.header_kind() == "Run command"
    assert resource.preview_text() is None


def test_command_resource_grant_preview_falls_back_to_literal_argv(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    resource = CommandResource(argv=("git", "push", "origin"))

    preview = resource.grant_preview(session_config)

    assert preview == GrantPreview(resource_text="git push origin")


def test_command_resource_grant_preview_promotes_a_matched_ask_rule(tmp_path: Path) -> None:
    session_config = SessionConfig(
        workspace=Workspace(path=tmp_path),
        command_rules=CommandRules(ask=[["git", "push", "?"]]))
    resource = CommandResource(argv=("git", "push", "origin"))

    preview = resource.grant_preview(session_config)

    assert preview == GrantPreview(resource_text="git push ?")


def test_command_resource_apply_grant_uses_explicit_patterns_when_given(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    resource = CommandResource(argv=("grep", "-rn", "TODO"))

    resource.apply_grant(
        "allow", "session", session_config, None, grant_patterns=[["grep", "**"]])

    assert session_config.command_rules.allow == [["grep", "**"]]


def test_command_resource_apply_grant_recomputes_when_patterns_omitted(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    resource = CommandResource(argv=("git", "status"))

    resource.apply_grant("allow", "session", session_config, None)

    assert session_config.command_rules.allow == [["git", "status"]]


def test_command_resource_added_to_override() -> None:
    resource = CommandResource(argv=("rm", "-rf", "build"))

    override = resource.added_to_override(PermissionOverride())

    assert override.commands == frozenset({("rm", "-rf", "build")})


# --- SkillResource ---


def test_skill_resource_header_kind_and_preview_text() -> None:
    resource = SkillResource(skill_id=("workspace", "add-cli-flag"))
    assert resource.header_kind() == "Activate skill"
    assert resource.preview_text() == "/add-cli-flag (workspace)"


def test_skill_resource_grant_preview_names_itself_with_no_widening(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    resource = SkillResource(skill_id=("internal", "some-skill"))

    preview = resource.grant_preview(session_config)

    assert preview == GrantPreview(resource_text="/some-skill (internal)")


def test_skill_resource_apply_grant_persists_to_session_config(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    resource = SkillResource(skill_id=("workspace", "add-cli-flag"))

    resource.apply_grant("allow", "session", session_config, None)

    assert session_config.skill_rules.allow == [("workspace", "add-cli-flag")]


def test_skill_resource_added_to_override() -> None:
    resource = SkillResource(skill_id=("workspace", "foo"))

    override = resource.added_to_override(PermissionOverride())

    assert override.skills == frozenset({("workspace", "foo")})


# --- DomainResource ---


def test_domain_resource_header_kind_and_preview_text_shows_the_full_url() -> None:
    resource = DomainResource(url="https://example.com/some/path?query=1")
    assert resource.header_kind() == "Fetch URL"
    assert resource.preview_text() == "https://example.com/some/path?query=1"


def test_domain_resource_domain_is_derived_from_the_url_not_stored_separately() -> None:
    """Regression test: `domain` must be parsed from `url` rather than a second, independently-
    settable field, since `url`-keyed display and `domain`-keyed grant/override checks would
    otherwise be able to silently drift apart."""
    resource = DomainResource(url="https://example.com:8080/some/path")
    assert resource.domain == "example.com"


def test_domain_resource_grant_preview_and_apply_grant_key_on_the_domain(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    resource = DomainResource(url="https://example.com/some/path")

    preview = resource.grant_preview(session_config)
    resource.apply_grant("allow", "session", session_config, None)

    assert preview == GrantPreview(resource_text="example.com")
    assert session_config.domain_rules.allow == ["example.com"]


def test_domain_resource_added_to_override_uses_the_domain_not_the_url() -> None:
    resource = DomainResource(url="https://example.com/some/path")

    override = resource.added_to_override(PermissionOverride())

    assert override.domains == frozenset({"example.com"})


# --- StructuralResource ---


def test_structural_resource_header_kind_and_preview_text() -> None:
    resource = StructuralResource(reason="a non-literal argument")
    assert resource.header_kind() == "Confirm"
    assert resource.preview_text() is None


def test_structural_resource_is_not_persistable() -> None:
    assert StructuralResource(reason="some reason").is_persistable is False


def test_structural_resource_grant_preview_is_none(tmp_path: Path) -> None:
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    assert StructuralResource(reason="some reason").grant_preview(session_config) is None


def test_structural_resource_apply_grant_is_a_noop(tmp_path: Path) -> None:
    """A structural item has no persistable rule -- `apply_grant` must not raise or mutate
    anything, matching `Session._retry_after_multi_permission_decisions`'s silent no-op
    fallthrough for a structural item's persistent-scope decision."""
    session_config = SessionConfig(workspace=Workspace(path=tmp_path))
    process_config = ProcessConfig()
    before_command_rules = session_config.command_rules
    before_read_dirs = session_config.read_dirs

    StructuralResource(reason="some reason").apply_grant(
        "allow", "workspace", session_config, process_config)

    assert session_config.command_rules == before_command_rules
    assert session_config.read_dirs == before_read_dirs


def test_structural_resource_added_to_override() -> None:
    resource = StructuralResource(reason="a non-literal argument")

    override = resource.added_to_override(PermissionOverride())

    assert override.reasons == frozenset({"a non-literal argument"})


# --- PermissionOverride (moved here from klorb.permissions.table) ---


def test_permission_override_equality_and_repr() -> None:
    a = PermissionOverride(paths=frozenset({Path("/f.txt")}))
    b = PermissionOverride(paths=frozenset({Path("/f.txt")}))
    c = PermissionOverride(paths=frozenset({Path("/g.txt")}))

    assert a == b
    assert a != c
    assert "PermissionOverride" in repr(a)


def test_permission_override_defaults_to_all_empty_sets() -> None:
    override = PermissionOverride()

    assert override.paths == frozenset()
    assert override.commands == frozenset()
    assert override.reasons == frozenset()
    assert override.skills == frozenset()
    assert override.domains == frozenset()
