# © Copyright 2026 Aaron Kimball
"""Shared mechanics behind `TodoList`/`TodoNext`/`TodoCreate`/`TodoUpdate`: discovering the
`chainlink` binary, the subprocess protocol (`--json`/`--quiet` handling and lock-contention
retry), one-time per-workspace setup, and the session-close cleanup callback. See
docs/specs/chainlink-task-tracking.md.
"""

import json
import logging
import os
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from klorb.tools.setup_context import ToolSetupContext

logger = logging.getLogger(__name__)

Priority = Literal["low", "medium", "high", "critical"]

CommentKind = Literal[
    "note", "plan", "decision", "observation", "blocker", "resolution", "result", "handoff",
    "human",
]

PRIORITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}
"""Sort weight for `TodoList`/`TodoNext`'s "priority, descending" ordering -- lower sorts
first, so `critical` (weight 0) comes before `low` (weight 3). An unrecognized priority string
falls back to `medium`'s weight (see `issue_sort_key`)."""

_CHAINLINK_DB_RELPATH = Path(".chainlink") / "issues.db"
_GITIGNORE_ENTRY = ".chainlink/"

_INIT_MANAGED_PATHS: tuple[Path, ...] = (
    Path(".claude") / "settings.json",
    Path(".claude") / "hooks",
    Path(".claude") / "mcp",
    Path(".mcp.json"),
)
"""Paths `chainlink --json init` plants alongside the `.chainlink/` task database itself: a
Claude-Code-specific hooks/MCP scaffold that has nothing to do with klorb's own use of chainlink
as a task-tracking backend (see docs/specs/chainlink-task-tracking.md's "Setup" section). Each
path here is relative to `self._workspace_root` -- `chainlink init` (and so `_ensure_setup`)
always runs with `cwd` set there (see `_run`), so this is always where `.claude/`/`.mcp.json`
land, never some other directory. `_ensure_setup()` snapshots which of these already exist
before running `init`, and afterward removes only the ones `init` itself created -- never a
path that was already there -- so a workspace that already has its own Claude Code
configuration is left untouched."""

_LOCK_ERROR_MARKER = "database is locked"
_LOCK_RETRY_ATTEMPTS = 4
_LOCK_RETRY_BASE_DELAY_SECONDS = 0.25
_LOCK_RETRY_JITTER_SECONDS = 0.025


def _discover_binary() -> Path | None:
    """Return the `chainlink` binary's path: `PATH` first (`shutil.which`), then
    `$VIRTUAL_ENV/bin/chainlink` if a Python virtualenv is active (klorb's own dev venv, e.g.,
    may have it installed alongside `venv/bin/klorb` itself rather than on the ambient `PATH`),
    then `$HOME/.cargo/bin/chainlink` as a last resort for an environment where `cargo install`
    put it somewhere not on `PATH` -- `make cloud_setup` installs it exactly that way (see
    `klorb/Makefile`'s `install_chainlink` target). `None` if none of these resolve."""
    on_path = shutil.which("chainlink")
    if on_path is not None:
        return Path(on_path)
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        venv_candidate = Path(virtual_env) / "bin" / "chainlink"
        if venv_candidate.is_file():
            return venv_candidate
    cargo_candidate = Path.home() / ".cargo" / "bin" / "chainlink"
    return cargo_candidate if cargo_candidate.is_file() else None


def chainlink_available() -> bool:
    """Return whether the `chainlink` binary can be found at all -- see `_discover_binary()`.
    Consulted by `ToolRegistry.discover_tools` to decide whether the `TASKS` tool category is
    registered for a session at all, and by `ChainlinkClient.__init__` to fail fast with a
    clear message instead of a bare "file not found" from `subprocess.run`."""
    return _discover_binary() is not None


class ChainlinkError(RuntimeError):
    """Raised when a `chainlink` subprocess call fails: a non-zero exit (after exhausting the
    lock-contention retry, if that's what triggered it) or the binary can't be found at all."""


def _subprocess_env() -> dict[str, str]:
    """This process's environment, plus `RUST_BACKTRACE=0` -- `chainlink` (a Rust binary)
    prints a full backtrace after its own `Error: ...` line whenever `RUST_BACKTRACE` is set in
    the caller's environment, which would otherwise bury the actual error message `ChainlinkError`
    wants to surface."""
    env = dict(os.environ)
    env["RUST_BACKTRACE"] = "0"
    return env


def _first_nonblank_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "chainlink exited with an error and produced no output."


def open_blocker_count(issue: dict[str, Any], open_ids: set[int]) -> int:
    """How many of `issue`'s `blocked_by` ids are currently open, per `open_ids` -- chainlink's
    own `blocked_by` list is never pruned as a blocker closes (a closed blocker still appears in
    it; see docs/specs/chainlink-task-tracking.md), so "still in the way" has to be computed by
    intersecting with the set of ids known to be open, not read off `blocked_by`'s length
    directly. `open_ids` is expected to be the id set of every issue `fetch_and_sort_issues()`
    already fetched for the same session label; a blocker belonging to a different label (an
    unusual cross-session dependency) is treated as not-open, since it was never fetched."""
    return len([blocker for blocker in issue.get("blocked_by", []) if blocker in open_ids])


def issue_sort_key(issue: dict[str, Any], open_ids: set[int]) -> tuple[int, int, int, int]:
    """`TodoList`/`TodoNext`'s shared sort order: open issues before closed, fewest open
    blockers first (0-blocker "ready" issues at the top), highest priority first, then lowest
    (oldest) id first."""
    return (
        0 if issue.get("status") == "open" else 1,
        open_blocker_count(issue, open_ids),
        PRIORITY_ORDER.get(issue.get("priority", "medium"), PRIORITY_ORDER["medium"]),
        issue.get("id", 0),
    )


class ChainlinkClient:
    """Thin wrapper around the `chainlink` CLI: binary discovery, `--json`/`--quiet` handling,
    lock-contention retry, and the handful of typed operations `TodoList`/`TodoNext`/
    `TodoCreate`/`TodoUpdate` need. Constructed fresh by each tool's `apply()` from its own
    `ToolSetupContext` (mirroring how a `Tool` itself is constructed fresh per call -- see
    `ToolRegistry.instantiate_tool`), never shared or cached across calls, so every one of the
    four `Tool`s shells out through this one class instead of each reimplementing the protocol.

    Every operation is scoped to one master session's issues via `label`, always
    `context.session.id` (see docs/specs/chainlink-task-tracking.md) -- constructing a
    `ChainlinkClient` without a real `Session` on `context` raises `ValueError`, the same
    contract `BashTool._execute_persistent` enforces for `session`-dependent functionality.

    `__init__` also performs one-time workspace setup (`_ensure_setup`, a cheap no-op after the
    first call) and registers this session's close-time cleanup callback
    (`Session.register_teardown`) -- both idempotent, so constructing a fresh `ChainlinkClient`
    on every `Tool.apply()` call is safe and cheap.
    """

    def __init__(self, context: "ToolSetupContext") -> None:
        binary = _discover_binary()
        if binary is None:
            raise ChainlinkError(
                "chainlink binary not found on PATH or at ~/.cargo/bin/chainlink")
        self._binary = binary
        self._workspace_root = context.session_config.workspace.path
        session = context.session
        if session is None:
            raise ValueError(
                "ChainlinkClient requires a Session on ToolSetupContext, to scope issues by "
                "session label.")
        self._label = session.id
        self._ensure_setup()
        session.register_teardown("ChainlinkClient", self._close_all_on_teardown)

    def _ensure_setup(self) -> None:
        """Run `chainlink --json init` and ensure `.chainlink/` is gitignored, but only the
        first time this workspace has no `.chainlink/issues.db` yet -- a cheap `Path.exists()`
        check on every other call. See docs/specs/chainlink-task-tracking.md's "Setup" section
        for why `init`'s own side effects beyond the issue database itself are pruned right
        afterward."""
        if (self._workspace_root / _CHAINLINK_DB_RELPATH).exists():
            return
        logger.debug(
            "No chainlink issue database in %s yet; running 'chainlink init'.",
            self._workspace_root)
        claude_dir = self._workspace_root / ".claude"
        claude_dir_preexisted = claude_dir.is_dir()
        preexisting = {
            managed for managed in _INIT_MANAGED_PATHS
            if (self._workspace_root / managed).exists()
        }
        result = subprocess.run(
            [str(self._binary), "--json", "init"], cwd=self._workspace_root,
            capture_output=True, text=True, env=_subprocess_env())
        if result.returncode != 0:
            raise ChainlinkError(f"chainlink init failed: {_first_nonblank_line(result.stderr)}")
        self._prune_unrelated_init_scaffold(preexisting, claude_dir, claude_dir_preexisted)
        self._ensure_gitignore_entry()

    def _prune_unrelated_init_scaffold(
        self, preexisting: set[Path], claude_dir: Path, claude_dir_preexisted: bool,
    ) -> None:
        """Delete the Claude-Code-hooks/MCP scaffold `chainlink init` just planted
        (`_INIT_MANAGED_PATHS`), keeping only what was already there before this call -- never a
        path `preexisting` already recorded as present. Also removes `.claude/` itself if `init`
        created it and pruning left it empty."""
        for managed in _INIT_MANAGED_PATHS:
            if managed in preexisting:
                continue
            planted = self._workspace_root / managed
            if not planted.exists():
                continue
            logger.debug("Removing chainlink-init-planted path unrelated to task tracking: %s", planted)
            if planted.is_dir():
                shutil.rmtree(planted)
            else:
                planted.unlink()
        if not claude_dir_preexisted and claude_dir.is_dir() and not any(claude_dir.iterdir()):
            claude_dir.rmdir()

    def _ensure_gitignore_entry(self) -> None:
        """Add `.chainlink/` to the workspace's top-level `.gitignore`, creating the file if it
        doesn't exist, unless a line already covers it. `issues.db` has no merge-conflict
        resolution story (chainlink assigns ids sequentially with no reconciliation across
        branches), so it must never be committed -- see docs/specs/chainlink-task-tracking.md."""
        gitignore_path = self._workspace_root / ".gitignore"
        try:
            existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
        except OSError:
            logger.warning(
                "Could not read %s to ensure it covers %r; leaving it as-is.",
                gitignore_path, _GITIGNORE_ENTRY)
            return
        covered = any(
            line.strip().rstrip("/") == _GITIGNORE_ENTRY.rstrip("/")
            for line in existing.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
        if covered:
            return
        logger.debug(
            "Adding %r to %s so the chainlink issue database is never committed.",
            _GITIGNORE_ENTRY, gitignore_path)
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with open(gitignore_path, "a", encoding="utf-8") as handle:
            handle.write(f"{prefix}{_GITIGNORE_ENTRY}\n")

    def _run(self, args: list[str]) -> "subprocess.CompletedProcess[str]":
        """Run `chainlink <args>` in the workspace root, retrying up to `_LOCK_RETRY_ATTEMPTS`
        times with exponential backoff (`_LOCK_RETRY_BASE_DELAY_SECONDS`, doubling each retry)
        plus uniform jitter in `[-_LOCK_RETRY_JITTER_SECONDS, +_LOCK_RETRY_JITTER_SECONDS]` when
        chainlink's shared SQLite file is locked by a concurrent session. Raises `ChainlinkError`
        (`stderr`'s first non-blank line -- `RUST_BACKTRACE=0` keeps that line free of a Rust
        backtrace) once every attempt fails, or immediately for any non-lock-contention failure.
        """
        command = [str(self._binary), *args]
        last_result: subprocess.CompletedProcess[str] | None = None
        for attempt in range(_LOCK_RETRY_ATTEMPTS):
            result = subprocess.run(
                command, cwd=self._workspace_root, capture_output=True, text=True,
                env=_subprocess_env())
            if result.returncode == 0:
                return result
            last_result = result
            is_lock_contention = _LOCK_ERROR_MARKER in result.stderr.lower()
            if not is_lock_contention or attempt == _LOCK_RETRY_ATTEMPTS - 1:
                break
            delay = _LOCK_RETRY_BASE_DELAY_SECONDS * (2 ** attempt)
            delay += random.uniform(-_LOCK_RETRY_JITTER_SECONDS, _LOCK_RETRY_JITTER_SECONDS)
            delay = max(delay, 0.0)
            logger.debug(
                "chainlink database locked (attempt %d/%d); retrying in %.3fs: %s",
                attempt + 1, _LOCK_RETRY_ATTEMPTS, delay, command)
            time.sleep(delay)
        assert last_result is not None
        raise ChainlinkError(
            f"chainlink {' '.join(args)} failed: {_first_nonblank_line(last_result.stderr)}")

    def list_issues(self, *, status: str = "open") -> list[dict[str, Any]]:
        """Return every issue under this client's label, in chainlink's own `list --json`
        shape (no `blocked_by`/`comments`/etc. -- see `fetch_and_sort_issues` for the enriched,
        sorted view `TodoList`/`TodoNext` actually return)."""
        result = self._run(["--json", "issue", "list", "--label", self._label, "--status", status])
        parsed: list[dict[str, Any]] = json.loads(result.stdout)
        return parsed

    def show_issue(self, issue_id: int) -> dict[str, Any]:
        """Return one issue's full detail (labels, comments, blocked_by/blocking, etc.)."""
        result = self._run(["--json", "issue", "show", str(issue_id)])
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed

    def create_issue(
        self, title: str, *, description: str | None = None, priority: Priority = "medium",
    ) -> int:
        """Create a new issue under this client's label and return its new id."""
        args = ["--quiet", "issue", "create", title, "--priority", priority, "--label", self._label]
        if description is not None:
            args.extend(["--description", description])
        result = self._run(args)
        return int(result.stdout.strip())

    def update_issue(
        self, issue_id: int, *, title: str | None = None, description: str | None = None,
        priority: Priority | None = None,
    ) -> None:
        """Update `issue_id`'s title/description/priority; a no-op if all three are `None`."""
        if title is None and description is None and priority is None:
            return
        args = ["--quiet", "issue", "update", str(issue_id)]
        if title is not None:
            args.extend(["--title", title])
        if description is not None:
            args.extend(["--description", description])
        if priority is not None:
            args.extend(["--priority", priority])
        self._run(args)

    def close_issue(self, issue_id: int) -> None:
        """Close `issue_id`. Always passes `--no-changelog`: chainlink's default close behavior
        writes an entry to a `CHANGELOG.md` at the workspace root, which has nothing to do with
        klorb's ephemeral, session-scoped task tracking and would otherwise silently mutate a
        real source file outside klorb's own permission-gated write tools."""
        self._run(["--quiet", "issue", "close", str(issue_id), "--no-changelog"])

    def reopen_issue(self, issue_id: int) -> None:
        self._run(["--quiet", "issue", "reopen", str(issue_id)])

    def block(self, issue_id: int, blocker_id: int) -> None:
        """Record that `issue_id` is blocked by `blocker_id`."""
        self._run(["--quiet", "issue", "block", str(issue_id), str(blocker_id)])

    def unblock(self, issue_id: int, blocker_id: int) -> None:
        """Remove the record that `issue_id` is blocked by `blocker_id`."""
        self._run(["--quiet", "issue", "unblock", str(issue_id), str(blocker_id)])

    def comment(self, issue_id: int, text: str, *, kind: CommentKind = "note") -> None:
        self._run(["--quiet", "issue", "comment", str(issue_id), text, "--kind", kind])

    def close_all(self) -> None:
        """Close every open issue under this client's label -- see `_close_all_on_teardown`,
        the only caller. Also always `--no-changelog`, for the same reason `close_issue` is."""
        self._run(["--quiet", "issue", "close-all", "--label", self._label, "--no-changelog"])

    def _close_all_on_teardown(self) -> None:
        """`Session.register_teardown` callback: close every remaining open issue under this
        label when the session ends (see docs/adrs/chainlink-issues-not-chainlink-sessions-
        for-continuity.md -- klorb uses chainlink ephemerally, one label per master session,
        with no "resume a prior session's leftover work" capability yet). Logged and swallowed
        rather than raised: `Session.close()` runs every registered teardown unconditionally and
        must not be interrupted by one of them failing."""
        try:
            self.close_all()
        except ChainlinkError:
            logger.warning(
                "Failed to close-all chainlink issues for label %r on session close.",
                self._label, exc_info=True)


def fetch_and_sort_issues(client: ChainlinkClient, *, include_closed: bool) -> list[dict[str, Any]]:
    """Fetch every issue under `client`'s label (`--status all` if `include_closed`, else just
    `--status open`), enrich each with its full `show_issue` detail (needed for `blocked_by`,
    which `list` alone doesn't return), and sort per `issue_sort_key`. Shared by `TodoList` and
    `TodoNext` so both use the exact same fetch-enrich-sort pipeline."""
    base = client.list_issues(status="all" if include_closed else "open")
    issues = [client.show_issue(issue["id"]) for issue in base]
    open_ids = {issue["id"] for issue in issues if issue.get("status") == "open"}
    issues.sort(key=lambda issue: issue_sort_key(issue, open_ids))
    return issues
