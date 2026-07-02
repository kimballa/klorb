# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions: PermissionsTable/DirectoryAccessTable evaluation, the
evaluate_write/resolve_and_evaluate_read policy functions, and find_workspace_root. See
docs/specs/permissions.md.
"""

from pathlib import Path

import pytest

from klorb.permissions.directory_access import DirectoryAccessTable, DirRules, find_workspace_root
from klorb.permissions.table import PermissionAskRequired, raise_if_not_allowed
from klorb.permissions.workspace import canonicalize_candidate, evaluate_write
from klorb.permissions.workspace import resolve_and_evaluate_read, resolve_within_workspace
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext


def _context(
    workspace_root: Path,
    *,
    is_workspace_trusted: bool = False,
    read_dirs: DirRules | None = None,
    write_dirs: DirRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(is_workspace_trusted=is_workspace_trusted),
        session_config=SessionConfig(
            workspace_root=workspace_root, read_dirs=read_dirs or DirRules(),
            write_dirs=write_dirs or DirRules()),
    )


# --- PermissionsTable / DirectoryAccessTable category-order evaluation ---


def test_deny_beats_allow_regardless_of_specificity(tmp_path: Path) -> None:
    project = tmp_path / "the_project"
    private = project / "private"
    private.mkdir(parents=True)

    table = DirectoryAccessTable(DirRules(deny=[private], allow=[project]))

    assert table.evaluate(project / "foo.txt") == "allow"
    assert table.evaluate(private / "nope.txt") == "deny"


def test_ask_wins_over_allow_but_loses_to_deny(tmp_path: Path) -> None:
    all_three = DirectoryAccessTable(DirRules(deny=[tmp_path], ask=[tmp_path], allow=[tmp_path]))
    assert all_three.evaluate(tmp_path / "f.txt") == "deny"

    ask_and_allow = DirectoryAccessTable(DirRules(ask=[tmp_path], allow=[tmp_path]))
    assert ask_and_allow.evaluate(tmp_path / "f.txt") == "ask"


def test_no_matching_rule_returns_none(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    table = DirectoryAccessTable(DirRules(allow=[elsewhere]))

    assert table.evaluate(tmp_path / "unrelated" / "f.txt") is None


def test_stricter_rule_wins_regardless_of_construction_order(tmp_path: Path) -> None:
    """The single most important invariant: a deny always beats an allow, no matter which was
    added to the table first — since concatenation across config layers means the final list
    order carries no evaluation precedence of its own."""
    looser_first = DirectoryAccessTable(DirRules(allow=[tmp_path], deny=[tmp_path / "secret"]))
    stricter_first = DirectoryAccessTable(DirRules(deny=[tmp_path / "secret"], allow=[tmp_path]))

    candidate = tmp_path / "secret" / "key.txt"
    assert looser_first.evaluate(candidate) == "deny"
    assert stricter_first.evaluate(candidate) == "deny"


def test_containment_includes_exact_match(tmp_path: Path) -> None:
    table = DirectoryAccessTable(DirRules(allow=[tmp_path]))
    assert table.evaluate(tmp_path) == "allow"


def test_symlinked_rule_directory_is_canonicalized(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real_dir)

    table = DirectoryAccessTable(DirRules(deny=[link]))

    assert table.evaluate(real_dir / "f.txt") == "deny"


def test_matches_a_pre_resolved_former_symlink_candidate_against_its_real_target(
    tmp_path: Path,
) -> None:
    """DirectoryAccessTable.evaluate() expects an already-canonicalized candidate (the caller's
    job — see canonicalize_candidate/resolve_within_workspace tests below); this confirms
    containment matching itself is correct once a symlink hop has been resolved away."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = real_dir / "link"
    link.symlink_to(outside)

    table = DirectoryAccessTable(DirRules(deny=[outside]))

    assert table.evaluate((link / "f.txt").resolve(strict=False)) == "deny"


def test_dotdot_traversal_is_canonicalized_before_matching(tmp_path: Path) -> None:
    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()

    table = DirectoryAccessTable(DirRules(allow=[project]))

    textually_inside_but_escapes = (project / ".." / "other" / "f.txt").resolve(strict=False)
    assert textually_inside_but_escapes == other / "f.txt"
    assert table.evaluate(textually_inside_but_escapes) is None


def test_dot_component_does_not_confuse_matching(tmp_path: Path) -> None:
    table = DirectoryAccessTable(DirRules(allow=[tmp_path / "." / "project"]))
    assert table.evaluate((tmp_path / "project" / "f.txt")) == "allow"


def test_trailing_slash_does_not_affect_matching(tmp_path: Path) -> None:
    table = DirectoryAccessTable(DirRules(allow=[Path(str(tmp_path) + "/")]))
    assert table.evaluate(tmp_path / "f.txt") == "allow"


# --- evaluate_write ---


def test_evaluate_write_fallback_allows_when_nothing_matches(tmp_path: Path) -> None:
    path = resolve_within_workspace(_context(tmp_path), "f.txt")
    assert evaluate_write(_context(tmp_path), path) == "allow"


def test_evaluate_write_restricts_in_workspace_path(tmp_path: Path) -> None:
    context = _context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))
    path = resolve_within_workspace(context, "f.txt")
    assert evaluate_write(context, path) == "deny"


def test_evaluate_write_denies_klorb_dir_even_with_empty_write_dirs(tmp_path: Path) -> None:
    context = _context(tmp_path)
    path = resolve_within_workspace(context, ".klorb/klorb-config.json")
    assert evaluate_write(context, path) == "deny"


def test_evaluate_write_denies_klorb_dir_even_with_writedirs_allow_covering_workspace(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, write_dirs=DirRules(allow=[tmp_path]))
    path = resolve_within_workspace(context, ".klorb/klorb-config.json")
    assert evaluate_write(context, path) == "deny"


# --- resolve_and_evaluate_read: untrusted (default) ---


def test_untrusted_read_raises_outside_workspace_before_any_table_is_consulted(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    context = _context(workspace, read_dirs=DirRules(allow=[tmp_path]))
    with pytest.raises(PermissionError):
        resolve_and_evaluate_read(context, str(outside))


def test_untrusted_read_fallback_allows_inside_workspace(tmp_path: Path) -> None:
    context = _context(tmp_path)
    _, verdict = resolve_and_evaluate_read(context, "f.txt")
    assert verdict == "allow"


def test_read_six_step_chain_order(tmp_path: Path) -> None:
    def verdict_for(read_dirs: DirRules, write_dirs: DirRules) -> str:
        context = _context(tmp_path, read_dirs=read_dirs, write_dirs=write_dirs)
        _, verdict = resolve_and_evaluate_read(context, "f.txt")
        return verdict

    empty = DirRules()
    assert verdict_for(DirRules(deny=[tmp_path]), empty) == "deny"
    assert verdict_for(DirRules(ask=[tmp_path]), empty) == "ask"
    assert verdict_for(DirRules(allow=[tmp_path]), empty) == "allow"
    assert verdict_for(empty, DirRules(deny=[tmp_path])) == "deny"
    assert verdict_for(empty, DirRules(ask=[tmp_path])) == "ask"
    assert verdict_for(empty, DirRules(allow=[tmp_path])) == "allow"


def test_readdirs_allow_wins_over_matching_writedirs_deny_for_same_path(tmp_path: Path) -> None:
    context = _context(tmp_path, read_dirs=DirRules(allow=[tmp_path]), write_dirs=DirRules(deny=[tmp_path]))
    _, verdict = resolve_and_evaluate_read(context, "f.txt")
    assert verdict == "allow"


# --- resolve_and_evaluate_read: trusted (not reachable by production code paths today) ---


def test_trusted_read_does_not_raise_merely_for_being_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    context = _context(workspace, is_workspace_trusted=True, read_dirs=DirRules(allow=[tmp_path]))
    path, verdict = resolve_and_evaluate_read(context, str(outside))

    assert path == outside
    assert verdict == "allow"


def test_trusted_read_fallback_denies_when_nothing_matches(tmp_path: Path) -> None:
    context = _context(tmp_path, is_workspace_trusted=True)
    _, verdict = resolve_and_evaluate_read(context, "f.txt")
    assert verdict == "deny"


# --- canonicalize_candidate vs resolve_within_workspace ---


def test_canonicalize_candidate_matches_resolve_within_workspace_for_in_workspace_paths(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    assert canonicalize_candidate(context, "sub/f.txt") == resolve_within_workspace(context, "sub/f.txt")


def test_canonicalize_candidate_does_not_raise_for_outside_workspace_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    context = _context(workspace)
    assert canonicalize_candidate(context, str(outside)) == outside

    with pytest.raises(PermissionError):
        resolve_within_workspace(context, str(outside))


# --- find_workspace_root ---


def test_find_workspace_root_finds_nearest_ancestor_klorb_dir(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / ".klorb").mkdir(parents=True)
    nested = project / "src" / "sub"
    nested.mkdir(parents=True)

    assert find_workspace_root(nested) == project


def test_find_workspace_root_falls_back_to_cwd_when_no_klorb_dir_found(tmp_path: Path) -> None:
    isolated = tmp_path / "isolated"
    isolated.mkdir()

    assert find_workspace_root(isolated) == isolated


def test_find_workspace_root_ignores_symlinked_klorb_dir(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    real_klorb = tmp_path / "real-klorb"
    real_klorb.mkdir()
    (project / ".klorb").symlink_to(real_klorb)

    assert find_workspace_root(project) == project.resolve(strict=False)


def test_find_workspace_root_ignores_klorb_file_not_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".klorb").write_text("not a directory")

    assert find_workspace_root(project) == project.resolve(strict=False)


# --- raise_if_not_allowed ---


def test_permission_ask_required_is_not_a_permission_error_subclass() -> None:
    """Deliberate design choice, not an oversight: PermissionAskRequired is a plain Exception,
    not a PermissionError subclass, specifically so future interactive-confirmation code can
    catch it on its own (`except PermissionAskRequired`) without also silently swallowing
    plain denials via a broader `except PermissionError`. See klorb.permissions.table's module
    docstring and docs/specs/permissions.md. If this assertion ever fails because someone
    added PermissionError as a base class, that's almost certainly the bug to revert, not this
    test to update.
    """
    assert not issubclass(PermissionAskRequired, PermissionError)


def test_raise_if_not_allowed_deny_raises_permission_error() -> None:
    with pytest.raises(PermissionError):
        raise_if_not_allowed("deny", resource_description="x")


def test_raise_if_not_allowed_ask_raises_permission_ask_required() -> None:
    with pytest.raises(PermissionAskRequired):
        raise_if_not_allowed("ask", resource_description="x")


def test_raise_if_not_allowed_allow_returns_normally() -> None:
    raise_if_not_allowed("allow", resource_description="x")
