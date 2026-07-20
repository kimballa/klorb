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
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pathspec import GitIgnoreSpec

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
first, so `critical` (weight 0) comes before `low` (weight 3). Also the canonical set of valid
priority strings, checked by `validate_priority`."""

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
    `$VIRTUAL_ENV/bin/chainlink` if a Python virtualenv is active, then `$HOME/.cargo/bin/
    chainlink` as a last resort -- `cargo install` puts a binary there whether or not
    `~/.cargo/bin` is itself on `PATH`, and `make cloud_setup` installs chainlink exactly that
    way (see `klorb/Makefile`'s `install_chainlink` target). `None` if none of these resolve."""
    on_path = shutil.which("chainlink")
    if on_path is not None:
        logger.debug("Found chainlink on PATH: %s", on_path)
        return Path(on_path)
    logger.debug("chainlink not found on PATH.")

    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        venv_candidate = Path(virtual_env) / "bin" / "chainlink"
        if venv_candidate.is_file():
            logger.debug("Found chainlink in the active virtualenv: %s", venv_candidate)
            return venv_candidate
        logger.debug("chainlink not found in the active virtualenv at %s.", venv_candidate)

    cargo_candidate = Path.home() / ".cargo" / "bin" / "chainlink"
    if cargo_candidate.is_file():
        logger.debug("Found chainlink at the cargo-install fallback path: %s", cargo_candidate)
        return cargo_candidate
    logger.debug("chainlink not found at the cargo-install fallback path %s either.", cargo_candidate)
    return None


def chainlink_available() -> bool:
    """Return whether the `chainlink` binary can be found at all -- see `_discover_binary()`.
    Consulted by `ToolRegistry.discover_tools` to decide whether the `TASKS` tool category is
    registered for a session at all, and by `ChainlinkClient.__init__` to fail fast with a
    clear message instead of a bare "file not found" from `subprocess.run`."""
    return _discover_binary() is not None


class ChainlinkError(RuntimeError):
    """Raised when a `chainlink` subprocess call fails: a non-zero exit (after exhausting the
    lock-contention retry, if that's what triggered it) or the binary can't be found at all.
    Carries the full command line, working directory, and exit code alongside `chainlink`'s own
    error output, so a caller sees enough to reproduce the failure by hand."""


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


def validate_priority(priority: str) -> None:
    """Raise `ValueError` if `priority` isn't one of `PRIORITY_ORDER`'s keys -- the JSON schema
    each `Tool.parameters()` declares already restricts a well-behaved model to a valid value,
    but `create_issue`/`update_issue` validate again here rather than trusting that up front."""
    if priority not in PRIORITY_ORDER:
        raise ValueError(f"priority must be one of {sorted(PRIORITY_ORDER)}, got {priority!r}")


def open_blocker_count(issue: dict[str, Any], open_ids: set[int]) -> int:
    """How many of `issue`'s `blocked_by` ids are currently open, per `open_ids` -- chainlink's
    own `blocked_by` list is never pruned as a blocker closes (a closed blocker still appears in
    it; see docs/specs/chainlink-task-tracking.md), so "still in the way" has to be computed by
    intersecting with the set of ids known to be open, not read off `blocked_by`'s length
    directly. `open_ids` is expected to be the id set of every issue `fetch_and_sort_issues()`
    already fetched for the same session label; a blocker belonging to a different label (an
    unusual cross-session dependency) is treated as not-open, since it was never fetched."""
    return len(list(filter(lambda blocker: blocker in open_ids, issue.get("blocked_by", []))))


def issue_sort_key(open_ids: set[int]) -> Callable[[dict[str, Any]], tuple[int, int, int, int]]:
    """Curried: return the sort-key function for `TodoList`/`TodoNext`'s shared sort order --
    open issues before closed, fewest open blockers first (0-blocker "ready" issues at the top),
    highest priority first, then lowest (oldest) id first -- closed over `open_ids` so a caller
    can pass the result straight to `list.sort(key=...)` without a wrapping lambda."""

    def key(issue: dict[str, Any]) -> tuple[int, int, int, int]:
        return (
            0 if issue.get("status") == "open" else 1,
            open_blocker_count(issue, open_ids),
            PRIORITY_ORDER.get(issue.get("priority", "medium"), PRIORITY_ORDER["medium"]),
            issue.get("id", 0),
        )

    return key


class ChainlinkClient:
    """Thin wrapper around the `chainlink` CLI: binary discovery, the `--json`/`--quiet`
    subprocess protocol, and lock-contention retry, so `TodoList`/`TodoNext`/`TodoCreate`/
    `TodoUpdate` each construct one from their own `ToolSetupContext` rather than shelling out
    independently. Never shared or cached across calls -- built fresh, cheaply, on every
    `Tool.apply()`.

    Every operation is scoped to one root session's issues via `label`
    (`Session.get_chainlink_label()`; see docs/specs/chainlink-task-tracking.md). Constructing a
    `ChainlinkClient` without a real `Session` on `context` raises `ValueError`: there is no
    label to scope issues by without one.

    `__init__` also performs one-time workspace setup (`_ensure_setup`, a cheap no-op after the
    first call) and registers this session's close-time cleanup callback
    (`Session.register_teardown`) -- both idempotent, so constructing a fresh `ChainlinkClient`
    on every `Tool.apply()` call is safe and cheap.
    """

    def __init__(self, context: "ToolSetupContext") -> None:
        binary = _discover_binary()
        if binary is None:
            raise ChainlinkError(
                "chainlink binary not found on PATH, in the active virtualenv, or at "
                "~/.cargo/bin/chainlink")
        self._binary = binary
        self._workspace_root = context.session_config.workspace.path
        session = context.session
        if session is None:
            raise ValueError(
                "ChainlinkClient requires a Session on ToolSetupContext, to scope issues by "
                "a chainlink label.")
        self._session = session
        self._label = session.get_chainlink_label()
        self._ensure_setup()
        session.register_teardown("ChainlinkClient", self._close_all_on_teardown)

    def _ensure_setup(self) -> None:
        """Run `chainlink init` and ensure `.chainlink/` is gitignored, but only the first time
        this workspace has no `.chainlink/issues.db` yet -- a cheap `Path.exists()` check on
        every other call. See docs/specs/chainlink-task-tracking.md's "Setup" section for why
        `init`'s own side effects beyond the issue database itself are pruned right afterward."""
        if (self._workspace_root / _CHAINLINK_DB_RELPATH).exists():
            return
        logger.debug(
            "No chainlink issue database in %s yet; running 'chainlink init'.",
            self._workspace_root)
        claude_dir = self._workspace_root / ".claude"
        claude_dir_preexisted = claude_dir.is_dir()
        preexisting = set(filter(
            lambda managed: (self._workspace_root / managed).exists(), _INIT_MANAGED_PATHS))

        self._run(["init"])

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
            logger.debug(
                "Removing now-empty %s (created by chainlink init, nothing else populated it).",
                claude_dir)
            claude_dir.rmdir()

    def _ensure_gitignore_entry(self) -> None:
        """Add `.chainlink/` to the workspace's top-level `.gitignore`, creating the file if it
        doesn't exist, unless the existing rules already cover it (checked via `pathspec.
        GitIgnoreSpec`, the same gitignore-matching library `klorb.tools.util.gitignore` uses,
        rather than a hand-rolled line comparison -- so a broader existing rule, e.g. a wildcard
        dotdir pattern, is correctly recognized as already covering it). `issues.db` has no
        merge-conflict resolution story (chainlink assigns ids sequentially with no
        reconciliation across branches), so it must never be committed -- see
        docs/specs/chainlink-task-tracking.md."""
        gitignore_path = self._workspace_root / ".gitignore"
        try:
            existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
        except OSError:
            logger.warning(
                "Could not read %s to ensure it covers %r; leaving it as-is.",
                gitignore_path, _GITIGNORE_ENTRY)
            return
        if GitIgnoreSpec.from_lines(existing.splitlines()).match_file(_GITIGNORE_ENTRY):
            return
        logger.debug(
            "Adding %r to %s so the chainlink issue database is never committed.",
            _GITIGNORE_ENTRY, gitignore_path)
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        with open(gitignore_path, "a", encoding="utf-8") as handle:
            handle.write(f"{prefix}{_GITIGNORE_ENTRY}\n")

    def _run(self, args: list[str], *, quiet: bool = False) -> "subprocess.CompletedProcess[str]":
        """Run `chainlink --json [--quiet] <args>` in the workspace root -- every call goes
        through here, the one place that builds the command line, retries, and raises, so no
        other method in this class shells out on its own. Retries up to `_LOCK_RETRY_ATTEMPTS`
        times with exponential backoff (`_LOCK_RETRY_BASE_DELAY_SECONDS`, doubling each retry)
        plus uniform jitter in `[-_LOCK_RETRY_JITTER_SECONDS, +_LOCK_RETRY_JITTER_SECONDS]` when
        chainlink's shared SQLite file is locked by a concurrent session; this relies on every
        `Tool.apply()` call already running off klorb's main thread (the TUI dispatches a whole
        turn, tool calls included, via a `@work(thread=True)` worker -- see `klorb.tui.mixins.
        prompt_submission`), so blocking here with `time.sleep` never freezes the UI. Raises
        `ChainlinkError` once every attempt fails, or immediately for any non-lock-contention
        failure.
        """
        flags = ["--json", *(["--quiet"] if quiet else [])]
        command = [str(self._binary), *flags, *args]
        logger.debug("Running chainlink command: %s (cwd=%s)", command, self._workspace_root)
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
            f"chainlink command failed: {command} (cwd={self._workspace_root}, "
            f"exit code {last_result.returncode}): {_first_nonblank_line(last_result.stderr)}")

    def list_issues(self, *, status: str = "open") -> list[dict[str, Any]]:
        """Return every issue under this client's label, in chainlink's own `list --json`
        shape (no `blocked_by`/`comments`/etc. -- see `fetch_and_sort_issues` for the enriched,
        sorted view `TodoList`/`TodoNext` actually return)."""
        result = self._run(["issue", "list", "--label", self._label, "--status", status])
        parsed: list[dict[str, Any]] = json.loads(result.stdout)
        return parsed

    def show_issue(self, issue_id: int) -> dict[str, Any]:
        """Return one issue's full detail (labels, comments, blocked_by/blocking, etc.)."""
        result = self._run(["issue", "show", str(issue_id)])
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed

    def create_issue(
        self, title: str, *, description: str | None = None, priority: Priority = "medium",
    ) -> int:
        """Create a new issue under this client's label and return its new id. `chainlink issue
        create` doesn't emit JSON regardless of `--json` (verified against the installed
        binary), so the new id is parsed from `--quiet`'s bare-value stdout instead."""
        validate_priority(priority)
        args = ["issue", "create", title, "--priority", priority, "--label", self._label]
        if description is not None:
            args.extend(["--description", description])
        result = self._run(args, quiet=True)
        return int(result.stdout.strip())

    def update_issue(
        self, issue_id: int, *, title: str | None = None, description: str | None = None,
        priority: Priority | None = None,
    ) -> None:
        """Update `issue_id`'s title/description/priority; a no-op if all three are `None`."""
        if title is None and description is None and priority is None:
            return
        if priority is not None:
            validate_priority(priority)
        args = ["issue", "update", str(issue_id)]
        if title is not None:
            args.extend(["--title", title])
        if description is not None:
            args.extend(["--description", description])
        if priority is not None:
            args.extend(["--priority", priority])
        self._run(args, quiet=True)

    def close_issue(self, issue_id: int) -> None:
        """Close `issue_id`. Always passes `--no-changelog`: chainlink's default close behavior
        writes an entry to a `CHANGELOG.md` at the workspace root, which has nothing to do with
        klorb's ephemeral, session-scoped task tracking and would otherwise silently mutate a
        real source file outside klorb's own permission-gated write tools."""
        self._run(["issue", "close", str(issue_id), "--no-changelog"], quiet=True)

    def reopen_issue(self, issue_id: int) -> None:
        self._run(["issue", "reopen", str(issue_id)], quiet=True)

    def block(self, issue_id: int, blocker_id: int) -> None:
        """Record that `issue_id` is blocked by `blocker_id`."""
        self._run(["issue", "block", str(issue_id), str(blocker_id)], quiet=True)

    def unblock(self, issue_id: int, blocker_id: int) -> None:
        """Remove the record that `issue_id` is blocked by `blocker_id`."""
        self._run(["issue", "unblock", str(issue_id), str(blocker_id)], quiet=True)

    def comment(self, issue_id: int, text: str, *, kind: CommentKind = "note") -> None:
        self._run(["issue", "comment", str(issue_id), text, "--kind", kind], quiet=True)

    def close_all(self) -> None:
        """Close every open issue under this client's label -- see `_close_all_on_teardown`,
        the only caller. Also always `--no-changelog`, for the same reason `close_issue` is."""
        self._run(["issue", "close-all", "--label", self._label, "--no-changelog"], quiet=True)

    def _close_all_on_teardown(self) -> None:
        """`Session.register_teardown` callback: close every remaining open issue under this
        label when the session ends (see docs/adrs/chainlink-issues-not-chainlink-sessions-
        for-continuity.md -- klorb uses chainlink ephemerally, one label per root session, with
        no "resume a prior session's leftover work" capability yet). Logged and swallowed rather
        than raised: `Session.close()` runs every registered teardown unconditionally and must
        not be interrupted by one of them failing."""
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
    issues = list(map(lambda entry: client.show_issue(entry["id"]), base))
    open_ids = {issue["id"] for issue in issues if issue.get("status") == "open"}
    issues.sort(key=issue_sort_key(open_ids))
    return issues
