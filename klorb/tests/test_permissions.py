# © Copyright 2026 Aaron Kimball
"""Tests for klorb.permissions: PermissionsTable/DirectoryAccessTable evaluation, the
evaluate_write/resolve_and_evaluate_read policy functions, and find_workspace_root. See
docs/specs/permissions.md.
"""

from pathlib import Path

import pytest

from klorb.paths import KLORB_CONFIG_DIR, KLORB_DATA_DIR, KLORB_STATE_DIR
from klorb.permissions.directory_access import (
    DirectoryAccessTable,
    DirRules,
    canonicalize_dir,
    find_workspace_root,
    is_privileged_path,
    privileged_dirs,
)
from klorb.permissions.file_access import FileRules
from klorb.permissions.table import PermissionAskRequired, PermissionOverride, raise_if_not_allowed
from klorb.permissions.workspace import (
    canonicalize_candidate,
    evaluate_write,
    resolve_and_evaluate_read,
    resolve_and_evaluate_write,
    resolve_within_workspace,
)
from klorb.process_config import ProcessConfig
from klorb.session import SessionConfig
from klorb.tools.setup_context import ToolSetupContext
from klorb.workspace import Workspace


def _context(
    workspace_root: Path,
    *,
    is_workspace_trusted: bool = False,
    read_dirs: DirRules | None = None,
    write_dirs: DirRules | None = None,
    read_files: FileRules | None = None,
    write_files: FileRules | None = None,
) -> ToolSetupContext:
    return ToolSetupContext(
        process_config=ProcessConfig(),
        session_config=SessionConfig(
            workspace=Workspace(path=workspace_root, trusted=is_workspace_trusted),
            read_dirs=read_dirs or DirRules(), write_dirs=write_dirs or DirRules(),
            read_files=read_files or FileRules(), write_files=write_files or FileRules()),
    )


# --- PermissionsTable / DirectoryAccessTable category-order evaluation ---


def test_deny_beats_allow_regardless_of_specificity(tmp_path: Path) -> None:
    project = tmp_path / "the_project"
    private = project / "private"
    private.mkdir(parents=True)

    table = DirectoryAccessTable(DirRules(deny=[private], allow=[project]), tmp_path)

    assert table.evaluate(project / "foo.txt") == "allow"
    assert table.evaluate(private / "nope.txt") == "deny"


def test_ask_wins_over_allow_but_loses_to_deny(tmp_path: Path) -> None:
    all_three = DirectoryAccessTable(DirRules(deny=[tmp_path], ask=[tmp_path], allow=[tmp_path]), tmp_path)
    assert all_three.evaluate(tmp_path / "f.txt") == "deny"

    ask_and_allow = DirectoryAccessTable(DirRules(ask=[tmp_path], allow=[tmp_path]), tmp_path)
    assert ask_and_allow.evaluate(tmp_path / "f.txt") == "ask"


def test_no_matching_rule_returns_none(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    table = DirectoryAccessTable(DirRules(allow=[elsewhere]), tmp_path)

    assert table.evaluate(tmp_path / "unrelated" / "f.txt") is None


def test_stricter_rule_wins_regardless_of_construction_order(tmp_path: Path) -> None:
    """The single most important invariant: a deny always beats an allow, no matter which was
    added to the table first — since concatenation across config layers means the final list
    order carries no evaluation precedence of its own."""
    looser_first = DirectoryAccessTable(DirRules(allow=[tmp_path], deny=[tmp_path / "secret"]), tmp_path)
    stricter_first = DirectoryAccessTable(DirRules(deny=[tmp_path / "secret"], allow=[tmp_path]), tmp_path)

    candidate = tmp_path / "secret" / "key.txt"
    assert looser_first.evaluate(candidate) == "deny"
    assert stricter_first.evaluate(candidate) == "deny"


def test_containment_includes_exact_match(tmp_path: Path) -> None:
    table = DirectoryAccessTable(DirRules(allow=[tmp_path]), tmp_path)
    assert table.evaluate(tmp_path) == "allow"


def test_symlinked_rule_directory_is_canonicalized(tmp_path: Path) -> None:
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real_dir)

    table = DirectoryAccessTable(DirRules(deny=[link]), tmp_path)

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

    table = DirectoryAccessTable(DirRules(deny=[outside]), tmp_path)

    assert table.evaluate((link / "f.txt").resolve(strict=False)) == "deny"


def test_dotdot_traversal_is_canonicalized_before_matching(tmp_path: Path) -> None:
    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()

    table = DirectoryAccessTable(DirRules(allow=[project]), tmp_path)

    textually_inside_but_escapes = (project / ".." / "other" / "f.txt").resolve(strict=False)
    assert textually_inside_but_escapes == other / "f.txt"
    assert table.evaluate(textually_inside_but_escapes) is None


def test_dot_component_does_not_confuse_matching(tmp_path: Path) -> None:
    table = DirectoryAccessTable(DirRules(allow=[tmp_path / "." / "project"]), tmp_path)
    assert table.evaluate((tmp_path / "project" / "f.txt")) == "allow"


def test_trailing_slash_does_not_affect_matching(tmp_path: Path) -> None:
    table = DirectoryAccessTable(DirRules(allow=[Path(str(tmp_path) + "/")]), tmp_path)
    assert table.evaluate(tmp_path / "f.txt") == "allow"


def test_relative_rule_path_is_resolved_against_workspace_root(tmp_path: Path) -> None:
    """Allow("sub") should mean the same thing as Allow("<workspace_root>/sub"), not a path
    relative to the process's current working directory."""
    workspace = tmp_path / "workspace"
    sub = workspace / "sub"
    sub.mkdir(parents=True)

    table = DirectoryAccessTable(DirRules(allow=[Path("sub")]), workspace)

    assert table.evaluate(sub / "f.txt") == "allow"
    assert table.evaluate(workspace / "other" / "f.txt") is None


def test_relative_dotdot_rule_path_escapes_workspace_root(tmp_path: Path) -> None:
    """Allow("..") means the same thing as Allow("<workspace_root>/.."), i.e. the workspace's
    parent directory — not the process's current working directory's parent."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    table = DirectoryAccessTable(DirRules(allow=[Path("..")]), workspace)

    assert table.evaluate(tmp_path) == "allow"
    assert table.evaluate(tmp_path / "sibling" / "f.txt") == "allow"


# --- privileged_dirs / is_privileged_path ---


def test_privileged_dirs_includes_klorb_dir_and_paths_dirs(tmp_path: Path) -> None:
    dirs = privileged_dirs(tmp_path)
    assert tmp_path / ".klorb" in dirs
    assert KLORB_CONFIG_DIR.resolve(strict=False) in dirs
    assert KLORB_DATA_DIR.resolve(strict=False) in dirs
    assert KLORB_STATE_DIR.resolve(strict=False) in dirs


def test_is_privileged_path_matches_descendants_not_unrelated_paths(tmp_path: Path) -> None:
    assert is_privileged_path(tmp_path / ".klorb" / "klorb-config.json", tmp_path)
    assert is_privileged_path(
        (KLORB_STATE_DIR / "session-logs" / "a.log").resolve(strict=False), tmp_path)
    assert not is_privileged_path(tmp_path / "src" / "main.py", tmp_path)


def test_canonicalize_dir_expands_home_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert canonicalize_dir(Path("~/.ssh"), workspace) == home / ".ssh"


def test_directory_access_table_deny_rule_with_tilde_matches_home_relative_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    table = DirectoryAccessTable(DirRules(deny=[Path("~/.ssh")]), workspace)
    assert table.evaluate(home / ".ssh" / "id_rsa") == "deny"


# --- evaluate_write ---


def test_evaluate_write_asks_when_nothing_matches_in_either_table(tmp_path: Path) -> None:
    """Zero config, for a write interrogation, is (None, None) -> ask/ask -- unlike read, write
    has no permissive no-match fallback; see test_write_merge_matrix below for the full
    (readDirs verdict, writeDirs verdict) -> write verdict table this is one cell of."""
    path = resolve_within_workspace(_context(tmp_path), "f.txt")
    assert evaluate_write(_context(tmp_path), path) == "ask"


def test_evaluate_write_allows_only_when_both_tables_explicitly_allow(tmp_path: Path) -> None:
    context = _context(
        tmp_path, read_dirs=DirRules(allow=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]))
    path = resolve_within_workspace(context, "f.txt")
    assert evaluate_write(context, path) == "allow"


def test_evaluate_write_asks_when_writedirs_allows_but_readdirs_is_silent(tmp_path: Path) -> None:
    """A writeDirs.allow entry alone does not grant write access to a path readDirs never
    mentions -- that would make the directory write-only, which is backwards: write access is
    never more permissive than read access for the same path."""
    context = _context(tmp_path, write_dirs=DirRules(allow=[tmp_path]))
    path = resolve_within_workspace(context, "f.txt")
    assert evaluate_write(context, path) == "ask"


def test_evaluate_write_denies_when_readdirs_denies_even_if_writedirs_allows(tmp_path: Path) -> None:
    context = _context(
        tmp_path, read_dirs=DirRules(deny=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]))
    path = resolve_within_workspace(context, "f.txt")
    assert evaluate_write(context, path) == "deny"


def test_evaluate_write_restricts_in_workspace_path(tmp_path: Path) -> None:
    context = _context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))
    path = resolve_within_workspace(context, "f.txt")
    assert evaluate_write(context, path) == "deny"


@pytest.mark.parametrize(("read_verdict", "write_verdict", "expected"), [
    ("deny", "deny", "deny"), ("deny", "ask", "deny"), ("deny", "allow", "deny"), ("deny", None, "deny"),
    ("ask", "deny", "deny"), ("ask", "ask", "ask"), ("ask", "allow", "ask"), ("ask", None, "ask"),
    ("allow", "deny", "deny"), ("allow", "ask", "ask"), ("allow", "allow", "allow"), ("allow", None, "ask"),
    (None, "deny", "deny"), (None, "ask", "ask"), (None, "allow", "ask"), (None, None, "ask"),
])
def test_write_merge_matrix(
    tmp_path: Path, read_verdict: str | None, write_verdict: str | None, expected: str,
) -> None:
    """Full 4x4 (readDirs verdict, writeDirs verdict) -> write verdict matrix: write access is
    the stricter of the two, with a table's "no matching rule" (None) normalized to "ask" for
    this write-side merge only (read's own no-match fallback, exercised separately below, is
    unaffected by this normalization)."""
    def rules(verdict: str | None) -> DirRules:
        if verdict is None:
            return DirRules()
        return DirRules(**{verdict: [tmp_path]})

    context = _context(tmp_path, read_dirs=rules(read_verdict), write_dirs=rules(write_verdict))
    path = resolve_within_workspace(context, "f.txt")
    assert evaluate_write(context, path) == expected


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


def test_evaluate_write_denies_klorb_state_dir_even_with_writedirs_allow(tmp_path: Path) -> None:
    context = _context(tmp_path, write_dirs=DirRules(allow=[KLORB_STATE_DIR]))
    path = (KLORB_STATE_DIR / "session-logs" / "a.log").resolve(strict=False)
    assert evaluate_write(context, path) == "deny"


# --- resolve_and_evaluate_write ---


def test_resolve_and_evaluate_write_falls_through_to_evaluate_write_when_no_file_rule_matches(
    tmp_path: Path,
) -> None:
    context = _context(
        tmp_path, read_dirs=DirRules(allow=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]))
    path, verdict = resolve_and_evaluate_write(context, "f.txt")
    assert path == tmp_path / "f.txt"
    assert verdict == "allow"


def test_resolve_and_evaluate_write_raises_outside_workspace_when_no_file_rule_matches(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    context = _context(workspace)
    with pytest.raises(PermissionError):
        resolve_and_evaluate_write(context, str(outside))


def test_writefiles_allow_bypasses_the_workspace_root_boundary(tmp_path: Path) -> None:
    """The motivating case: an exact writeFiles.allow entry for a path outside the workspace
    root (e.g. a character device) must resolve to "allow" rather than being rejected by the
    hard boundary resolve_within_workspace() would otherwise raise for it."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    device = tmp_path / "outside-device"

    context = _context(workspace, write_files=FileRules(allow=[device]))
    path, verdict = resolve_and_evaluate_write(context, str(device))

    assert path == device
    assert verdict == "allow"


def test_writefiles_deny_short_circuits_even_when_writedirs_would_allow(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    context = _context(
        tmp_path, write_dirs=DirRules(allow=[tmp_path]), read_dirs=DirRules(allow=[tmp_path]),
        write_files=FileRules(deny=[target]))
    _, verdict = resolve_and_evaluate_write(context, "f.txt")
    assert verdict == "deny"


def test_writefiles_ask_short_circuits_even_when_writedirs_would_deny(tmp_path: Path) -> None:
    """An exact writeFiles.ask entry is authoritative -- it is used as-is, not merged/stricter'd
    against a writeDirs.deny for the same path, unlike evaluate_write()'s own readDirs/writeDirs
    merge."""
    target = tmp_path / "f.txt"
    context = _context(tmp_path, write_dirs=DirRules(deny=[tmp_path]), write_files=FileRules(ask=[target]))
    _, verdict = resolve_and_evaluate_write(context, "f.txt")
    assert verdict == "ask"


def test_writefiles_privileged_path_still_denied_even_with_writefiles_allow(tmp_path: Path) -> None:
    """is_privileged_path() is checked before writeFiles -- no writeFiles.allow entry can
    re-enable access to .klorb/, mirroring evaluate_write()'s own unconditional deny."""
    target = tmp_path / ".klorb" / "klorb-config.json"
    context = _context(tmp_path, write_files=FileRules(allow=[target]))
    _, verdict = resolve_and_evaluate_write(context, ".klorb/klorb-config.json")
    assert verdict == "deny"


# --- readFiles: file-level short-circuit ahead of readDirs/the workspace boundary ---


def test_readfiles_allow_bypasses_the_workspace_root_boundary(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    device = tmp_path / "outside-device"

    context = _context(workspace, read_files=FileRules(allow=[device]))
    path, verdict = resolve_and_evaluate_read(context, str(device))

    assert path == device
    assert verdict == "allow"


def test_readfiles_deny_short_circuits_even_when_readdirs_would_allow(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    context = _context(tmp_path, read_dirs=DirRules(allow=[tmp_path]), read_files=FileRules(deny=[target]))
    _, verdict = resolve_and_evaluate_read(context, "f.txt")
    assert verdict == "deny"


def test_readfiles_has_no_opinion_falls_through_to_readdirs(tmp_path: Path) -> None:
    """A readFiles entry for a different file must not affect evaluation of this one -- it
    falls through to the ordinary readDirs/workspace-boundary flow untouched."""
    other = tmp_path / "other.txt"
    context = _context(tmp_path, read_dirs=DirRules(deny=[tmp_path]), read_files=FileRules(allow=[other]))
    _, verdict = resolve_and_evaluate_read(context, "f.txt")
    assert verdict == "deny"


def test_readfiles_privileged_path_still_denied_even_with_readfiles_allow(tmp_path: Path) -> None:
    target = tmp_path / ".klorb" / "klorb-config.json"
    context = _context(tmp_path, read_files=FileRules(allow=[target]))
    _, verdict = resolve_and_evaluate_read(context, ".klorb/klorb-config.json")
    assert verdict == "deny"


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


def test_read_uses_only_readdirs(tmp_path: Path) -> None:
    def verdict_for(read_dirs: DirRules) -> str:
        context = _context(tmp_path, read_dirs=read_dirs)
        _, verdict = resolve_and_evaluate_read(context, "f.txt")
        return verdict

    assert verdict_for(DirRules(deny=[tmp_path])) == "deny"
    assert verdict_for(DirRules(ask=[tmp_path])) == "ask"
    assert verdict_for(DirRules(allow=[tmp_path])) == "allow"


def test_read_ignores_writedirs_entirely(tmp_path: Path) -> None:
    """writeDirs never widens or narrows a read verdict: readDirs.evaluate() returning None
    (no match at all) falls straight through to read's own fallback, never consulting
    writeDirs."""
    denied_by_write = _context(tmp_path, write_dirs=DirRules(deny=[tmp_path]))
    _, verdict = resolve_and_evaluate_read(denied_by_write, "f.txt")
    assert verdict == "allow"

    allowed_by_write_denied_by_read = _context(
        tmp_path, read_dirs=DirRules(deny=[tmp_path]), write_dirs=DirRules(allow=[tmp_path]))
    _, verdict = resolve_and_evaluate_read(allowed_by_write_denied_by_read, "f.txt")
    assert verdict == "deny"


def test_untrusted_read_denies_klorb_dir_even_with_readdirs_allow_covering_workspace(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, read_dirs=DirRules(allow=[tmp_path]))
    _, verdict = resolve_and_evaluate_read(context, ".klorb/klorb-config.json")
    assert verdict == "deny"


# --- resolve_and_evaluate_read: trusted (see docs/specs/projects-and-trust.md) ---


def test_trusted_read_does_not_raise_merely_for_being_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"

    context = _context(workspace, is_workspace_trusted=True, read_dirs=DirRules(allow=[tmp_path]))
    path, verdict = resolve_and_evaluate_read(context, str(outside))

    assert path == outside
    assert verdict == "allow"


def test_trusted_read_fallback_asks_when_nothing_matches(tmp_path: Path) -> None:
    """No implicit "inside the workspace" allow, but also no implicit outright deny -- an
    unmentioned path reaches the same interactive-ask fallback evaluate_write() already uses."""
    context = _context(tmp_path, is_workspace_trusted=True)
    _, verdict = resolve_and_evaluate_read(context, "f.txt")
    assert verdict == "ask"


def test_trusted_read_denies_klorb_config_dir_even_with_readdirs_allow(tmp_path: Path) -> None:
    context = _context(
        tmp_path, is_workspace_trusted=True, read_dirs=DirRules(allow=[KLORB_CONFIG_DIR]))
    target = str((KLORB_CONFIG_DIR / "klorb-config.json").resolve(strict=False))
    _, verdict = resolve_and_evaluate_read(context, target)
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


def test_canonicalize_candidate_expands_home_tilde_for_llm_supplied_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model-supplied `filename` of `"~/.ssh/id_rsa"` must resolve to the real home directory
    (so a readDirs/writeDirs deny on `~/.ssh` actually catches it), not a literal `~`
    subdirectory joined onto workspace_root — see canonicalize_dir's docstring."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    context = _context(workspace)
    assert canonicalize_candidate(context, "~/.ssh/id_rsa") == home / ".ssh" / "id_rsa"


def test_resolve_within_workspace_raises_for_tilde_path_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    context = _context(workspace)
    with pytest.raises(PermissionError):
        resolve_within_workspace(context, "~/.ssh/id_rsa")


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


def test_raise_if_not_allowed_ask_populates_path_and_is_write() -> None:
    target = Path("/some/file.txt")
    with pytest.raises(PermissionAskRequired) as exc_info:
        raise_if_not_allowed("ask", resource_description="x", path=target, is_write=True)
    assert exc_info.value.path == target
    assert exc_info.value.is_write is True


def test_raise_if_not_allowed_ask_defaults_path_and_is_write_when_omitted() -> None:
    """The two tests above (test_raise_if_not_allowed_ask_raises_permission_ask_required, etc.)
    call raise_if_not_allowed with only resource_description, exactly as every pre-existing
    call site outside the four file tools does; path/is_write must stay optional so those
    keep working unchanged."""
    with pytest.raises(PermissionAskRequired) as exc_info:
        raise_if_not_allowed("ask", resource_description="x")
    assert exc_info.value.path is None
    assert exc_info.value.is_write is False


# --- PermissionsTable.matching_rules ---


def test_matching_rules_returns_every_match_in_the_category(tmp_path: Path) -> None:
    broad = tmp_path / "broad"
    narrow = tmp_path / "broad" / "narrow"
    unrelated = tmp_path / "unrelated"
    narrow.mkdir(parents=True)
    unrelated.mkdir()

    table = DirectoryAccessTable(DirRules(ask=[broad, narrow, unrelated]), tmp_path)
    matches = table.matching_rules("ask", narrow / "f.txt")

    assert set(matches) == {broad, narrow}


def test_matching_rules_returns_empty_list_for_no_match(tmp_path: Path) -> None:
    table = DirectoryAccessTable(DirRules(ask=[tmp_path / "elsewhere"]), tmp_path)
    assert table.matching_rules("ask", tmp_path / "other" / "f.txt") == []


def test_matching_rules_respects_category(tmp_path: Path) -> None:
    table = DirectoryAccessTable(DirRules(deny=[tmp_path], allow=[tmp_path]), tmp_path)
    assert table.matching_rules("ask", tmp_path / "f.txt") == []
    assert table.matching_rules("deny", tmp_path / "f.txt") == [tmp_path]
    assert table.matching_rules("allow", tmp_path / "f.txt") == [tmp_path]


# --- ToolSetupContext.permission_override ("Allow once") ---


def test_permission_override_short_circuits_evaluate_write_to_allow(tmp_path: Path) -> None:
    path = resolve_within_workspace(_context(tmp_path), "f.txt")
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=SessionConfig(workspace=Workspace(path=tmp_path)),
        permission_override=PermissionOverride(paths=frozenset({path})))
    assert evaluate_write(context, path) == "allow"


def test_permission_override_short_circuits_resolve_and_evaluate_read_to_allow(tmp_path: Path) -> None:
    path = resolve_within_workspace(_context(tmp_path), "f.txt")
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=SessionConfig(workspace=Workspace(path=tmp_path)),
        permission_override=PermissionOverride(paths=frozenset({path})))
    _, verdict = resolve_and_evaluate_read(context, "f.txt")
    assert verdict == "allow"


def test_permission_override_has_no_effect_on_a_different_path(tmp_path: Path) -> None:
    path = resolve_within_workspace(_context(tmp_path), "f.txt")
    other_path = resolve_within_workspace(_context(tmp_path), "other.txt")
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=SessionConfig(workspace=Workspace(path=tmp_path)),
        permission_override=PermissionOverride(paths=frozenset({other_path})))
    assert evaluate_write(context, path) == "ask"


def test_permission_override_never_bypasses_privileged_path_deny(tmp_path: Path) -> None:
    """Safety-critical: "Allow once" must never be usable to reach .klorb/ or the process-wide
    KLORB_*_DIR locations, even if a caller somehow set the override to that exact path."""
    path = resolve_within_workspace(_context(tmp_path), ".klorb/klorb-config.json")
    context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=SessionConfig(workspace=Workspace(path=tmp_path)),
        permission_override=PermissionOverride(paths=frozenset({path})))
    assert evaluate_write(context, path) == "deny"

    read_context = ToolSetupContext(
        process_config=ProcessConfig(), session_config=SessionConfig(workspace=Workspace(path=tmp_path)),
        permission_override=PermissionOverride(paths=frozenset({path})))
    _, verdict = resolve_and_evaluate_read(read_context, ".klorb/klorb-config.json")
    assert verdict == "deny"
