# © Copyright 2026 Aaron Kimball
"""Tests for klorb.sandbox: the bwrap availability smoke test and its cache, the mount-set
derivation (`compute_sandbox_dirs`), and the `bwrap` argv builder (`build_bwrap_argv`), including
end-to-end sandboxed execution on hosts where `bwrap` actually works. See
docs/specs/bash-tool-and-command-permissions.md's "Sandboxing" section.
"""

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from klorb import sandbox
from klorb.permissions.directory_access import DirRules, privileged_dirs
from klorb.permissions.file_access import FileRules
from klorb.sandbox import allowed_dir_snapshot, build_bwrap_argv, compute_sandbox_dirs

requires_bwrap = pytest.mark.skipif(
    not sandbox.bwrap_available(),
    reason="bwrap cannot create a sandbox here (missing binary or no unprivileged user namespaces)")
"""Skip (never xfail/error) tests that need a real, working `bwrap` — shared with the runtime's
own `bwrap_available()` gate rather than a separate Docker-detection heuristic, per the plan's
'klorb's own test suite' note. Container/cloud-agent CI environments legitimately can't run these;
a WSL2/bare-metal dev host can."""


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    sandbox.reset_availability_cache()
    yield
    sandbox.reset_availability_cache()


def test_unavailable_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.bwrap_available() is False
    assert sandbox.detect_bwrap_unavailable_reason() == "missing_binary"


def test_unavailable_when_smoke_test_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=1))
    assert sandbox.bwrap_available() is False
    assert sandbox.detect_bwrap_unavailable_reason() == "no_userns"


def test_available_when_binary_present_and_smoke_test_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=0))
    assert sandbox.bwrap_available() is True
    assert sandbox.detect_bwrap_unavailable_reason() is None


def test_result_is_cached_across_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_which(name: str) -> str:
        calls["n"] += 1
        return "/usr/bin/bwrap"

    monkeypatch.setattr(sandbox.shutil, "which", fake_which)
    monkeypatch.setattr(
        sandbox.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=0))

    assert sandbox.bwrap_available() is True
    assert sandbox.bwrap_available() is True
    assert calls["n"] == 1


def test_reset_cache_forces_a_fresh_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.bwrap_available() is False

    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        sandbox.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode=0))
    assert sandbox.bwrap_available() is False  # still cached

    sandbox.reset_availability_cache()
    assert sandbox.bwrap_available() is True


# --- compute_sandbox_dirs (mount-set derivation from the permission tables) ---


def test_home_is_always_read_write(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    dirs = compute_sandbox_dirs(
        workspace_root=tmp_path / "ws", home=home, trusted=False,
        read_dirs=DirRules(), write_dirs=DirRules())
    assert home.resolve() in dirs.read_write


def test_trusted_workspace_is_read_write_untrusted_is_read_only(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    # An out-of-home workspace so the whole-home bind doesn't already cover it.
    trusted = compute_sandbox_dirs(
        workspace_root=ws, home=tmp_path / "home", trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules())
    assert ws.resolve() in trusted.read_write
    assert ws.resolve() not in trusted.read_only

    untrusted = compute_sandbox_dirs(
        workspace_root=ws, home=tmp_path / "home", trusted=False,
        read_dirs=DirRules(), write_dirs=DirRules())
    assert ws.resolve() in untrusted.read_only
    assert ws.resolve() not in untrusted.read_write


def test_write_dirs_allow_becomes_read_write(tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    dirs = compute_sandbox_dirs(
        workspace_root=tmp_path / "ws", home=tmp_path / "home", trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(allow=[extra]))
    assert extra.resolve() in dirs.read_write


def test_read_dirs_deny_and_privileged_dirs_are_masked(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    secret = tmp_path / "home" / ".ssh"
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=tmp_path / "home", trusted=True,
        read_dirs=DirRules(deny=[secret]), write_dirs=DirRules())
    assert secret.resolve() in dirs.mask
    # <workspace>/.klorb and the process-wide klorb dirs come from the shared privileged-dir list.
    for privileged in privileged_dirs(ws):
        assert privileged in dirs.mask


def test_read_allow_under_a_write_allow_is_not_double_bound(tmp_path: Path) -> None:
    parent = tmp_path / "proj"
    child = parent / "sub"
    dirs = compute_sandbox_dirs(
        workspace_root=tmp_path / "ws", home=tmp_path / "home", trusted=True,
        read_dirs=DirRules(allow=[child]), write_dirs=DirRules(allow=[parent]))
    assert parent.resolve() in dirs.read_write
    assert child.resolve() not in dirs.read_only


def test_allowed_dir_snapshot_grows_when_an_allow_is_added(tmp_path: Path) -> None:
    home = tmp_path / "home"
    base = compute_sandbox_dirs(
        workspace_root=tmp_path / "ws", home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules())
    grown = compute_sandbox_dirs(
        workspace_root=tmp_path / "ws", home=home, trusted=True,
        read_dirs=DirRules(allow=[tmp_path / "newly_granted"]), write_dirs=DirRules())
    assert allowed_dir_snapshot(base) < allowed_dir_snapshot(grown)


def test_read_files_deny_becomes_a_mask_file_when_it_exists(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    secret = home / ".git-credentials"
    secret.write_text("token")
    missing = home / ".netrc"  # not created
    dirs = compute_sandbox_dirs(
        workspace_root=tmp_path / "ws", home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(),
        read_files=FileRules(deny=[secret, missing]))
    assert secret.resolve() in dirs.mask_files
    assert missing.resolve() not in dirs.mask_files  # nothing to hide -> not masked


def test_allow_files_become_read_and_write_bind_sets(tmp_path: Path) -> None:
    readable = tmp_path / "r.conf"
    readable.write_text("r")
    writable = tmp_path / "w.conf"
    writable.write_text("w")
    dirs = compute_sandbox_dirs(
        workspace_root=tmp_path / "ws", home=tmp_path / "home", trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(),
        read_files=FileRules(allow=[readable]), write_files=FileRules(allow=[writable]))
    assert readable.resolve() in dirs.read_files
    assert writable.resolve() in dirs.write_files


def test_allowed_dir_snapshot_grows_when_a_file_allow_is_added(tmp_path: Path) -> None:
    home = tmp_path / "home"
    granted_file = tmp_path / "granted.conf"
    granted_file.write_text("x")
    base = compute_sandbox_dirs(
        workspace_root=tmp_path / "ws", home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules())
    grown = compute_sandbox_dirs(
        workspace_root=tmp_path / "ws", home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(),
        read_files=FileRules(allow=[granted_file]))
    assert allowed_dir_snapshot(base) < allowed_dir_snapshot(grown)


# --- build_bwrap_argv (static shape) ---


def _argv(tmp_path: Path) -> list[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules())
    return build_bwrap_argv(
        workspace_root=ws, home=home, env={"HOME": str(home), "USER": "u"}, dirs=dirs)


def test_argv_has_the_mandatory_namespaces_and_hardening(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    for flag in (
        "--unshare-net", "--unshare-ipc", "--unshare-pid", "--unshare-uts", "--unshare-cgroup",
        "--unshare-user", "--disable-userns", "--die-with-parent", "--new-session", "--clearenv",
    ):
        assert flag in argv, flag
    assert argv[argv.index("--hostname") + 1] == sandbox.SANDBOX_HOSTNAME
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[-1] == "--"


def test_argv_clears_and_sets_exactly_the_given_env(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    # Every --setenv NAME VALUE triple comes from the env dict we passed, nothing else.
    setenv_names = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--setenv"]
    assert set(setenv_names) == {"HOME", "USER"}


def test_argv_masks_come_after_the_home_bind(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    secret = home / ".ssh"
    secret.mkdir()
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(deny=[secret]), write_dirs=DirRules())
    argv = build_bwrap_argv(
        workspace_root=ws, home=home, env={"HOME": str(home)}, dirs=dirs)
    # The home bind exposes ~/.ssh; the mask must be applied afterward to win.
    home_bind_idx = next(
        i for i, tok in enumerate(argv) if tok == "--bind" and argv[i + 1] == str(home.resolve()))
    mask_idx = next(
        i for i, tok in enumerate(argv) if tok == "--tmpfs" and argv[i + 1] == str(secret.resolve()))
    assert mask_idx > home_bind_idx


def test_argv_chdir_is_the_workspace_root(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    assert argv[argv.index("--chdir") + 1] == str((tmp_path / "ws").resolve())


def test_argv_tmpfs_tmp_is_world_writable_and_sticky(tmp_path: Path) -> None:
    # /tmp is the only writable entry in the sandbox's temp-dir search path (--tmpfs /var shadows
    # /var/tmp, there is no /usr/tmp), so it must be writable regardless of which uid the userns
    # maps the command to -- a default 0755 tmpfs is writable only by its owner uid. It is mounted
    # 1777 (world-writable + sticky, like a real /tmp). See
    # docs/adrs/sandbox-tmp-is-1777-so-any-uid-can-write.md.
    argv = _argv(tmp_path)
    tmpfs_tmp = next(
        i for i, tok in enumerate(argv) if tok == "--tmpfs" and argv[i + 1] == "/tmp")
    # --perms applies to the immediately following mount op, so it must sit right before --tmpfs /tmp.
    assert argv[tmpfs_tmp - 2] == "--perms"
    assert argv[tmpfs_tmp - 1] == "1777"


def test_argv_masks_a_denied_file_inside_home_with_dev_null(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    secret = home / ".git-credentials"
    secret.write_text("token")
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(), read_files=FileRules(deny=[secret]))
    argv = build_bwrap_argv(workspace_root=ws, home=home, env={"HOME": str(home)}, dirs=dirs)
    idx = next(
        i for i, tok in enumerate(argv)
        if tok == "--ro-bind" and argv[i + 1] == "/dev/null" and argv[i + 2] == str(secret.resolve()))
    assert idx > 0


def test_argv_binds_an_allowed_file_outside_all_dirs_with_dir_parents(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "opt" / "cfg"
    outside.mkdir(parents=True)
    allowed = outside / "token.conf"
    allowed.write_text("x")
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(), read_files=FileRules(allow=[allowed]))
    argv = build_bwrap_argv(workspace_root=ws, home=home, env={"HOME": str(home)}, dirs=dirs)
    # The file itself is ro-bound, and its parent directories are synthesized with --dir first.
    bind_idx = next(
        i for i, tok in enumerate(argv)
        if tok == "--ro-bind" and argv[i + 1] == str(allowed.resolve())
        and argv[i + 2] == str(allowed.resolve()))
    parent = str(outside.resolve())
    dir_idx = next(i for i, tok in enumerate(argv) if tok == "--dir" and argv[i + 1] == parent)
    assert dir_idx < bind_idx


def test_argv_does_not_rebind_an_allowed_file_already_inside_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    allowed = home / "already-readable.conf"  # under the whole-home bind already
    allowed.write_text("x")
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(), read_files=FileRules(allow=[allowed]))
    argv = build_bwrap_argv(workspace_root=ws, home=home, env={"HOME": str(home)}, dirs=dirs)
    assert str(allowed.resolve()) not in argv  # no redundant self-bind


# --- end-to-end execution through a real bwrap sandbox ---


def _run_sandboxed(argv_prefix: list[str], script: str) -> subprocess.CompletedProcess[str]:
    inner = ["/bin/bash", "-c", script]
    return subprocess.run(argv_prefix + inner, capture_output=True, text=True, timeout=30)


@requires_bwrap
def test_sandbox_reports_the_fake_hostname_and_identity_uid(tmp_path: Path) -> None:
    import os

    argv = _argv(tmp_path)
    result = _run_sandboxed(argv, 'echo "$(hostname) $(id -u)"')
    assert result.returncode == 0
    hostname, uid = result.stdout.split()
    assert hostname == sandbox.SANDBOX_HOSTNAME
    assert uid == str(os.getuid())  # identity uid mapping -> files stay owned by the real user


@requires_bwrap
def test_sandbox_masks_a_denied_home_subdirectory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    secret_dir = home / ".ssh"
    secret_dir.mkdir()
    (secret_dir / "id_rsa").write_text("TOP SECRET KEY")
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(deny=[secret_dir]), write_dirs=DirRules())
    argv = build_bwrap_argv(workspace_root=ws, home=home, env={"HOME": str(home)}, dirs=dirs)
    result = _run_sandboxed(argv, 'cat "$HOME/.ssh/id_rsa" 2>&1; echo "rc=$?"')
    assert "TOP SECRET KEY" not in result.stdout
    assert "rc=0" not in result.stdout  # the file isn't there to read


@requires_bwrap
def test_sandbox_denies_network(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    result = _run_sandboxed(
        argv, 'getent ahosts example.com >/dev/null 2>&1 && echo RESOLVED || echo BLOCKED')
    assert result.stdout.strip() == "BLOCKED"


@requires_bwrap
def test_sandbox_write_lands_in_the_workspace_owned_by_the_real_user(tmp_path: Path) -> None:
    import os

    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(allow=[ws]))
    argv = build_bwrap_argv(workspace_root=ws, home=home, env={"HOME": str(home)}, dirs=dirs)
    result = _run_sandboxed(argv, 'echo hi > written.txt')
    assert result.returncode == 0
    written = ws / "written.txt"  # --chdir put us in ws; the rw bind let the write through
    assert written.read_text() == "hi\n"
    assert written.stat().st_uid == os.getuid()


@requires_bwrap
def test_sandbox_propagates_a_signal_death_as_128_plus_signum(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    # Pipeline forces bash to fork the python child, so bash observes its signal death and itself
    # exits 128+SIGSEGV(11)=139 -- the positive-code path klorb.tools.bash._decode_exit handles.
    result = _run_sandboxed(
        argv, 'true | python3 -c "import os,signal; os.kill(os.getpid(), signal.SIGSEGV)"')
    assert result.returncode == 139


@requires_bwrap
def test_sandbox_masks_a_denied_file_but_leaves_its_siblings_readable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    secret = home / ".git-credentials"
    secret.write_text("https://user:TOKEN@github.com")
    sibling = home / ".gitconfig"
    sibling.write_text("[user] name = Someone")
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(), read_files=FileRules(deny=[secret]))
    argv = build_bwrap_argv(workspace_root=ws, home=home, env={"HOME": str(home)}, dirs=dirs)
    result = _run_sandboxed(
        argv, 'cat "$HOME/.git-credentials" 2>/dev/null; echo "---"; cat "$HOME/.gitconfig"')
    assert "TOKEN" not in result.stdout  # denied file masked
    assert "Someone" in result.stdout  # its sibling in the same directory still readable


@requires_bwrap
def test_sandbox_binds_an_allowed_file_outside_every_directory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    # A file outside home and workspace, which no directory bind would otherwise carry in.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    allowed = outside / "allowed.conf"
    allowed.write_text("ALLOWED-BYTES")
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(), read_files=FileRules(allow=[allowed]))
    argv = build_bwrap_argv(workspace_root=ws, home=home, env={"HOME": str(home)}, dirs=dirs)
    result = _run_sandboxed(argv, f'cat {allowed}')
    assert result.returncode == 0
    assert result.stdout.strip() == "ALLOWED-BYTES"


@requires_bwrap
def test_sandbox_write_file_grant_outside_workspace_is_writable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    target = outside / "log.txt"
    target.write_text("")  # must exist to be bound
    dirs = compute_sandbox_dirs(
        workspace_root=ws, home=home, trusted=True,
        read_dirs=DirRules(), write_dirs=DirRules(), write_files=FileRules(allow=[target]))
    argv = build_bwrap_argv(workspace_root=ws, home=home, env={"HOME": str(home)}, dirs=dirs)
    result = _run_sandboxed(argv, f'echo appended > {target}')
    assert result.returncode == 0
    assert target.read_text() == "appended\n"  # the write reached the host file through the bind
