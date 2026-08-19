# © Copyright 2026 Aaron Kimball
"""Detects whether `bwrap` can actually sandbox `BashTool`'s subprocesses on this host, and
builds the `bwrap` argv that does so. See docs/specs/bash-tool-and-command-permissions.md.
"""

import logging
import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from klorb.paths import get_klorb_data_dir, get_klorb_state_dir
from klorb.permissions.directory_access import (
    DirRules,
    canonicalize_dir,
    privileged_dirs,
    workspace_klorb_dir,
)
from klorb.permissions.file_access import FileRules, is_wildcard_rule

logger = logging.getLogger(__name__)

BWRAP_BINARY_NAME = "bwrap"

SANDBOX_HOSTNAME = "klorb-host"
"""The fake hostname `--hostname` sets inside the sandbox."""

_USR_MERGE_LINKS = ("/bin", "/sbin", "/lib", "/lib64", "/libx32")
"""Top-level directories that are symlinks into `/usr` on a merged-`/usr` host and real
directories on a non-merged one. `_base_filesystem_args()` reproduces whichever layout the host
actually has so the sandbox's root matches the host's rather than synthesizing a merged view that
isn't real."""

DISPOSABLE_TMPFS_DIRS = (Path("/tmp"), Path("/var"))
"""Mount points `build_bwrap_argv()` covers with a fresh, disposable `--tmpfs`. A directory bind
whose target is *exactly* one of these is dropped rather than emitted: binding the host's `/tmp`
on top would clobber the scratch tmpfs the sandbox deliberately put there. That matters most for
`/tmp`. Only an *exact* match is dropped; a bind of a directory *under* `/tmp`/`/var` still lands
on top of the tmpfs, as intended."""

BwrapUnavailableReason = Literal["missing_binary", "no_userns"]
"""Why `bwrap_available()` returned `False`, for `BashTool`'s one-time fallback notice:
`"missing_binary"` needs a different fix than `"no_userns"` does."""

_SMOKE_TEST_ARGV = [
    BWRAP_BINARY_NAME, "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--", "true"]
"""A minimal `bwrap` invocation that only succeeds if this host permits `bwrap` to create the
implicit user namespace it always needs, regardless of which namespaces a real invocation would go
on to `--unshare-*`. Deliberately not a real sandbox shape."""

_availability_cache: bool | None = None
"""Cached result of `bwrap_available()`'s smoke test, for the life of this process. `None` means
not yet probed."""


def reset_availability_cache() -> None:
    """Clear the cached `bwrap_available()` result, so the next call re-runs the smoke test."""
    global _availability_cache
    _availability_cache = None


def bwrap_available() -> bool:
    """Return whether `bwrap` can actually create a sandbox on this host right now: the binary
    exists on `PATH` and a minimal smoke-test invocation succeeds. Cached for the life of this
    process after the first call.

    This is the single source of truth for "can `BashTool` sandbox its subprocess": no
    `/.dockerenv`/`cgroup` fingerprinting drives this decision, only an actual attempt to do the
    thing. Those heuristics are still useful for tailoring the unavailable-reason message, just
    never for the go/no-go decision itself.
    """
    global _availability_cache
    if _availability_cache is not None:
        return _availability_cache
    if shutil.which(BWRAP_BINARY_NAME) is None:
        _availability_cache = False
        return False
    try:
        result = subprocess.run(_SMOKE_TEST_ARGV, capture_output=True, timeout=10)
        _availability_cache = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("bwrap smoke test failed to run: %s", exc)
        _availability_cache = False
    return _availability_cache


def detect_bwrap_unavailable_reason() -> BwrapUnavailableReason | None:
    """Return why `bwrap_available()` is `False`, or `None` if it's actually available or hasn't
    been probed yet. Only used to tailor `BashTool`'s one-time fallback notice; never consulted
    for the go/no-go decision itself.
    """
    if shutil.which(BWRAP_BINARY_NAME) is None:
        return "missing_binary"
    if not bwrap_available():
        return "no_userns"
    return None


def bwrap_binary_path() -> str:
    """Absolute path to the `bwrap` binary, or the bare name as a last-resort fallback.
    `build_bwrap_argv()` uses this for argv[0] rather than the bare `"bwrap"` so
    `subprocess.Popen` finds it even when `BashTool` passes an explicit `env=` with no `PATH` of
    its own."""
    return shutil.which(BWRAP_BINARY_NAME) or BWRAP_BINARY_NAME


def path_dirs_from_env() -> list[Path]:
    """The klorb process's own `$PATH` directories, canonicalized, as a best-effort source for
    the PATH-derived top-up binds `build_bwrap_argv()` adds for toolchains installed outside
    `/usr`/`$HOME`. This is a narrow top-up, not the primary mechanism, so a directory already
    under one of those is dropped by `build_bwrap_argv()` rather than bound twice."""
    raw = os.environ.get("PATH", "")
    dirs: list[Path] = []
    for entry in raw.split(os.pathsep):
        if entry:
            dirs.append(Path(entry).resolve(strict=False))
    return dirs


@dataclass(frozen=True)
class SandboxDirs:
    """The three directory sets `build_bwrap_argv()` turns into `bwrap` binds, all canonicalized
    so they compare and nest correctly. Derived from a session's permission tables by
    `compute_sandbox_dirs()`."""

    read_write: tuple[Path, ...]
    """Directories bound read-write (`--bind`): the home directory, the workspace root when the
    workspace is trusted, and every `writeDirs.allow` entry."""
    read_only: tuple[Path, ...]
    """Directories bound read-only (`--ro-bind`): every `readDirs.allow` entry not already
    covered by a read-write bind, plus the workspace root when the workspace is *untrusted*."""
    mask: tuple[Path, ...]
    """Directories hidden with an empty `--tmpfs` overlay: every `readDirs.deny` entry and every
    `privileged_dirs()` entry. Applied last in the argv so they override any read-write bind that
    would otherwise expose them. The workspace's own `<workspace>/.klorb` dir is deliberately
    *not* here."""
    mask_files: tuple[Path, ...]
    """Individual files hidden with a `--ro-bind /dev/null` overlay: every existing `readFiles.deny`
    entry. `build_bwrap_argv` only actually masks the ones that land inside a directory the sandbox
    binds (a file outside every bind is already unreachable); files under a directory already in
    `mask` are skipped, since that `--tmpfs` overlay hides them wholesale already."""
    read_files: tuple[Path, ...]
    """Individual existing files to bind read-only (`--ro-bind`), from `readFiles.allow`.
    `build_bwrap_argv` only binds the ones that *aren't* already reachable: a file inside a bound
    directory is left alone, but one that lives outside every directory bind is bound into place,
    synthesizing its parent directories with `--dir` as needed. Lets an exact `readFiles.allow`
    grant for a path outside the workspace actually be readable inside the sandbox."""
    write_files: tuple[Path, ...]
    """Individual existing files to bind read-write (`--bind`), from `writeFiles.allow`."""
    workspace_config_dir: Path | None = None
    """The workspace's own `<workspace>/.klorb` directory, bound so a sandboxed command sees its
    managed files present rather than an empty `--tmpfs`. `None` only if the workspace root can't
    be resolved. Bound read-only by default (`workspace_config_writable=False`) and read-write
    once the session has a `scope="workspace"` escalation. `build_bwrap_argv` emits it after the
    directory masks so this ro/rw decision wins over the whole-workspace bind for that subtree."""
    workspace_config_writable: bool = False
    """Whether `workspace_config_dir` is bound read-write. `False` binds it read-only: visible to
    `git`, but the managed klorb settings can't be modified from the shell."""


def compute_sandbox_dirs(
    *,
    workspace_root: Path,
    home: Path,
    trusted: bool,
    read_dirs: DirRules,
    write_dirs: DirRules,
    read_files: FileRules | None = None,
    write_files: FileRules | None = None,
    approved_scopes: set[str] | None = None,
) -> SandboxDirs:
    """Derive the `SandboxDirs` bind sets from a session's permission tables. Every rule path is
    canonicalized against `workspace_root` exactly as `DirectoryAccessTable`/`FileAccessTable`
    canonicalize it, so a `~`-relative or workspace-relative rule maps to the same on-disk path
    the classification layer would check.

    The home directory is always read-write, with sensitive subdirectories masked back out via the
    `mask` set and individual `readFiles.deny` files masked out via the `mask_files` set, rather
    than enumerated as separate binds. Individual `readFiles.allow`/`writeFiles.allow` files carry
    into `read_files`/`write_files` so an exact file grant outside every directory bind is still
    reachable inside the sandbox.

    A `readFiles`/`writeFiles` entry containing `*` is skipped entirely here, in every category
    — this function only ever binds or masks concrete on-disk paths, never glob-expands a pattern
    against the directories it's about to bind. A wildcard `deny` entry is therefore enforced only
    by the tool-level `FileAccessTable` classification, not by the `bwrap` boundary: a sandboxed
    shell command can still read a file a wildcard rule would deny the agent's own file tools.

    `approved_scopes` is the session's `EscalatePrivileges` grants. It gates the workspace's own
    `<workspace>/.klorb` dir: that dir is always *bound* rather than masked, read-only by default
    and read-write once `"workspace"` is approved. The process-wide `KLORB_*_DIR` locations stay
    in the `mask` set regardless.
    """
    workspace_root = workspace_root.resolve(strict=False)
    home = home.resolve(strict=False)
    approved_scopes = approved_scopes or set()

    def canon(paths: Iterable[Path]) -> list[Path]:
        return [canonicalize_dir(path, workspace_root) for path in paths]

    read_write = {home}
    if trusted:
        read_write.add(workspace_root)
    read_write.update(canon(write_dirs.allow))

    read_only = set(canon(read_dirs.allow))
    if not trusted:
        read_only.add(workspace_root)
    read_only = {
        d for d in read_only
        if not any(d == w or d.is_relative_to(w) for w in read_write)}

    # The workspace `.klorb/` dir is bound (visible), not masked, so managed settings show up to
    # git instead of reading as deleted. Drop it from the mask set regardless of whether
    # `privileged_dirs` still lists it (it omits it once `"workspace"` is approved), and bind it
    # separately below — read-write only after that same escalation.
    workspace_config = workspace_klorb_dir(workspace_root)
    mask = set(canon(read_dirs.deny)) | set(privileged_dirs(workspace_root, approved_scopes))
    mask.discard(workspace_config)

    def existing_file_rules(rules: FileRules | None, attr: str) -> set[Path]:
        if rules is None:
            return set()
        raw = getattr(rules, attr)
        concrete = [f for f in raw if not is_wildcard_rule(f)]
        if len(concrete) != len(raw):
            logger.debug(
                "compute_sandbox_dirs: skipping %d wildcard %s file rule(s) -- not "
                "glob-expanded for bwrap masking, enforced only by FileAccessTable",
                len(raw) - len(concrete), attr)
        return {f for f in canon(concrete) if f.exists() and not f.is_dir()}

    mask_files = existing_file_rules(read_files, "deny")
    read_allow_files = existing_file_rules(read_files, "allow")
    write_allow_files = existing_file_rules(write_files, "allow")

    return SandboxDirs(
        read_write=tuple(sorted(read_write, key=str)),
        read_only=tuple(sorted(read_only, key=str)),
        mask=tuple(sorted(mask, key=str)),
        mask_files=tuple(sorted(mask_files, key=str)),
        read_files=tuple(sorted(read_allow_files, key=str)),
        write_files=tuple(sorted(write_allow_files, key=str)),
        workspace_config_dir=workspace_config,
        workspace_config_writable="workspace" in approved_scopes)


def allowed_dir_snapshot(dirs: SandboxDirs) -> frozenset[Path]:
    """The set of bound paths used by the persistent shell's reconcile-on-grow check: a live
    sandbox is rebuilt only when this set *grows* between commands. The `mask`/`mask_files` sets
    are deliberately excluded.

    The workspace `.klorb/` dir is included only once it becomes *writable*, so that escalation
    grows the set and rebuilds the live shell with `.klorb` now read-write; its read-only
    pre-escalation binding is stable and left out so it never churns the snapshot."""
    snapshot = (
        frozenset(dirs.read_write) | frozenset(dirs.read_only)
        | frozenset(dirs.read_files) | frozenset(dirs.write_files))
    if dirs.workspace_config_dir is not None and dirs.workspace_config_writable:
        snapshot = snapshot | {dirs.workspace_config_dir}
    return snapshot


def _is_covered(path: Path, roots: Sequence[Path]) -> bool:
    """Whether `path` is one of, or nested under, any directory in `roots` — so it's already
    reachable through that root's bind and doesn't need its own."""
    return any(path == root or path.is_relative_to(root) for root in roots)


def _base_filesystem_args() -> list[str]:
    """`--ro-bind` for the whole `/usr` and `/etc` trees, plus the top-level merged-`/usr`
    symlinks (or real-directory binds on a non-merged host) — see `_USR_MERGE_LINKS`. Whole
    trees, not just `/usr/bin`+`/usr/lib`: this picks up `/usr/local`, `/usr/share`, locale data,
    nsswitch.conf, ssl certs, passwd/group, etc. that real toolchains and libc calls lean on."""
    args = ["--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc"]
    for link_name in _USR_MERGE_LINKS:
        path = Path(link_name)
        if path.is_symlink():
            args += ["--symlink", os.readlink(link_name), link_name]
        elif path.is_dir():
            args += ["--ro-bind", link_name, link_name]
    return args


def _emit_bind(
    args: list[str], created: set[Path], base_roots: Sequence[Path], target: Path, mode: str,
) -> None:
    """Append a `--dir`-for-each-missing-parent then `<mode> target target` bind to `args`,
    tracking already-created parents in `created` so a shared ancestor isn't `--dir`'d twice.
    `mode` is `"--bind"` (read-write) or `"--ro-bind"` (read-only). Parents already reachable
    through a base bind (`base_roots`) are skipped — bwrap creates the final mount point itself."""
    for ancestor in reversed(target.parents):
        if ancestor == Path("/") or _is_covered(ancestor, base_roots) or ancestor in created:
            continue
        args += ["--dir", str(ancestor)]
        created.add(ancestor)
    args += [mode, str(target), str(target)]
    created.add(target)


def build_bwrap_argv(
    *,
    workspace_root: Path,
    home: Path,
    env: Mapping[str, str],
    dirs: SandboxDirs,
    path_dirs: Sequence[Path] = (),
    hostname: str = SANDBOX_HOSTNAME,
) -> list[str]:
    """Build the `bwrap` argv prefix for one sandboxed shell invocation, up to and including the
    `--` separator.

    Shape:

    * Namespaces: `--unshare-net`, `--unshare-ipc`, `--unshare-pid`, `--unshare-uts`,
      `--unshare-cgroup`, plus `--unshare-user`/`--disable-userns`.
    * Hardening: `--hostname`, `--die-with-parent`, `--new-session`, `--cap-drop ALL`.
    * Environment: `--clearenv` then one `--setenv` per `env` entry, so the sandboxed command
      starts from exactly the dict `build_bash_env()` built and nothing of klorb's own
      environment leaks in.
    * Filesystem: whole-tree read-only `/usr`+`/etc` and the merged-`/usr` symlinks; disposable
      `--tmpfs /tmp`, `--tmpfs /var`, `--dev /dev`, `--proc /proc`; a read-write whole-tree `$HOME`
      bind; read-write binds for `dirs.read_write` and read-only binds for `dirs.read_only` beyond
      what those already cover; PATH-derived read-only top-up binds for `path_dirs`; an empty
      `--tmpfs` mask over every `dirs.mask` directory; a bind of the workspace's own `.klorb/` dir
      applied after the masks; individual binds for `dirs.read_files`/`dirs.write_files` that
      aren't already reachable; and finally a `--ro-bind /dev/null` mask over every reachable
      `dirs.mask_files` file. The masks are applied after the binds so they win over the whole-home
      bind that would otherwise expose `~/.ssh`, `~/.git-credentials`, and friends.
    * `--chdir workspace_root` so the command starts in the workspace.
    """
    workspace_root = workspace_root.resolve(strict=False)
    home = home.resolve(strict=False)

    args: list[str] = [bwrap_binary_path()]
    args += [
        "--unshare-net", "--unshare-ipc", "--unshare-pid", "--unshare-uts", "--unshare-cgroup",
        "--unshare-user", "--disable-userns",
        "--hostname", hostname,
        "--die-with-parent", "--new-session", "--cap-drop", "ALL",
    ]

    args += ["--clearenv"]
    for name in sorted(env):
        args += ["--setenv", name, env[name]]

    args += _base_filesystem_args()

    # Disposable scratch mounts go on *before* the binds so a bound directory that happens to
    # live under /tmp or /var (e.g. a workspace root beneath a system temp dir) lands on top of
    # the fresh tmpfs rather than being wiped out by a tmpfs applied after it. A later bind whose
    # target *is* one of these mount points (`/tmp`, `/var`) is dropped entirely below rather than
    # allowed to clobber the scratch tmpfs -- see DISPOSABLE_TMPFS_DIRS.
    #
    # `/tmp` is mounted `--perms 1777` (world-writable + sticky, exactly like a real system `/tmp`)
    # rather than bwrap's default `0755`, so it is writable regardless of which uid the sandbox's
    # user namespace maps the command to. Insurance: the sandbox leaves `/tmp` as the only writable
    # entry in the standard temp-dir search path (`--tmpfs /var` shadows `/var/tmp`, there is no
    # `/usr/tmp` under the read-only `/usr` bind), so if `/tmp` were ever not writable
    # `tempfile.gettempdir()` (and pytest's `tmp_path`) would fall through to `os.getcwd()` -- the
    # workspace root -- and scatter temp dirs into the user's checkout. See
    # docs/adrs/00114-sandbox-tmp-is-1777-so-any-uid-can-write.md.
    args += [
        "--perms", "1777", "--tmpfs", "/tmp", "--tmpfs", "/var", "--dev", "/dev", "--proc", "/proc"]

    usr = Path("/usr")
    etc = Path("/etc")
    base_roots = [usr, etc, home]
    created: set[Path] = {usr, etc, home}
    args += ["--bind", str(home), str(home)]

    emitted: set[Path] = set()
    for d in dirs.read_write:
        if d in DISPOSABLE_TMPFS_DIRS or _is_covered(d, [home]) or not d.exists():
            continue
        _emit_bind(args, created, base_roots, d, "--bind")
        emitted.add(d)

    for d in dirs.read_only:
        if (d in DISPOSABLE_TMPFS_DIRS or _is_covered(d, base_roots)
                or _is_covered(d, list(emitted)) or not d.exists()):
            continue
        _emit_bind(args, created, base_roots, d, "--ro-bind")
        emitted.add(d)

    for d in path_dirs:
        if (d in DISPOSABLE_TMPFS_DIRS or _is_covered(d, base_roots)
                or _is_covered(d, list(emitted)) or not d.is_dir()):
            continue
        _emit_bind(args, created, base_roots, d, "--ro-bind")
        emitted.add(d)

    for d in dirs.mask:
        if d.exists():
            args += ["--tmpfs", str(d)]

    # The klorb data/state dirs ($HOME/.local/share/klorb, $HOME/.local/state/klorb) are masked
    # just above via privileged_dirs()'s --tmpfs. But the bundled tiktoken cache klorb init copied
    # into $KLORB_DATA_DIR/tiktoken-cache is read-only data the sandboxed command needs to read
    # (tiktoken reads it via $TIKTOKEN_CACHE_DIR, passed through by shareEnv), and $KLORB_STATE_DIR
    # holds session logs a sandboxed command may want to read. Re-bind both read-only *after* the
    # mask so the cache/logs are visible without giving the sandboxed command write access (or
    # exposing anything beyond what klorb itself put there). Applied after dirs.mask's --tmpfs and
    # before the individual-file masks below.
    for klorb_dir in (
        get_klorb_data_dir().resolve(strict=False),
        get_klorb_state_dir().resolve(strict=False),
    ):
        if klorb_dir.exists():
            args += ["--ro-bind", str(klorb_dir), str(klorb_dir)]

    # The workspace's own .klorb/ dir is bound (not masked) so a sandboxed `git status` sees its
    # managed files present rather than reporting them deleted. Emitted here -- after the directory
    # masks and after the whole-workspace bind -- so this ro/rw decision wins for that subtree:
    # read-only by default, read-write only after a scope=workspace escalation. Its parent
    # (the workspace root) is already bound, so no --dir synthesis is needed.
    if dirs.workspace_config_dir is not None and dirs.workspace_config_dir.exists():
        mode = "--bind" if dirs.workspace_config_writable else "--ro-bind"
        args += [mode, str(dirs.workspace_config_dir), str(dirs.workspace_config_dir)]

    # Individual allowed files that aren't already reachable: bind each one into place so an exact
    # readFiles/writeFiles grant for a path outside every directory bind (or for a single file
    # inside an otherwise-masked directory) actually works. A file already carried in by a real
    # directory bind, and not hidden by a mask, is left alone. `_emit_bind` synthesizes any missing
    # parent directories with `--dir` first. Emitted after the directory masks (so a file inside a
    # masked directory can be punched back through) but before the denied-file masks below (so a
    # path that is somehow in both an allow and a deny list still ends up denied). Write binds win
    # over read binds for a path named in both.
    real_roots = [*base_roots, *emitted]
    special_roots = [Path("/dev"), Path("/proc")]

    def _needs_file_bind(target: Path) -> bool:
        if _is_covered(target, special_roots):
            return False  # provided by --dev/--proc; don't fight those mounts
        return _is_covered(target, dirs.mask) or not _is_covered(target, real_roots)

    file_bound: set[Path] = set()
    for f in dirs.write_files:
        if _needs_file_bind(f):
            _emit_bind(args, created, base_roots, f, "--bind")
            file_bound.add(f)
    for f in dirs.read_files:
        if f not in file_bound and _needs_file_bind(f):
            _emit_bind(args, created, base_roots, f, "--ro-bind")
            file_bound.add(f)

    # Individual denied files: mask each one that is reachable -- inside a directory the sandbox
    # binds, or itself individually allow-bound just above (a path misconfigured into both an
    # allow and a deny list: deny must still win) -- unless its parent directory is already
    # wholesale-masked above (that `--tmpfs` hid it already). `--ro-bind /dev/null` is the standard
    # bwrap idiom for masking a single file's contents while leaving its siblings readable: the
    # mask makes reads of the secret fail rather than return its real bytes.
    for f in dirs.mask_files:
        if _is_covered(f, dirs.mask):
            continue
        if not (_is_covered(f, real_roots) or f in file_bound):
            continue
        args += ["--ro-bind", "/dev/null", str(f)]

    args += ["--chdir", str(workspace_root), "--"]
    return args
