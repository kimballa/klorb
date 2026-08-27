# © Copyright 2026 Aaron Kimball
"""Shared mechanics behind the task-tracking tools: binary discovery, subprocess protocol,
workspace setup, and session-close cleanup. See docs/specs/chainlink-task-tracking.md.
"""

import json
import logging
import os
import platform
import random
import shutil
import subprocess
import time
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
"""Sort weight for priority-descending ordering. Also the canonical set of valid priority
strings."""

ALL_LABEL = "all"
"""The chainlink label marking an issue as group-wide and unclaimed."""

AGENT_LABEL_PREFIX = "agent:"
"""Prefix of the chainlink label naming one agent's claim on an issue."""


def agent_label(agent_id: str) -> str:
    """The chainlink label naming `agent_id`'s claim on an issue."""
    return f"{AGENT_LABEL_PREFIX}{agent_id}"


CHAINLINK_CLIENT_TOOL_STATE_KEY = "tasks"
"""Key under which the cached `ChainlinkClient` is stored in `session.tool_state`."""

TASK_TOOL_NAMES: frozenset[str] = frozenset({"TodoList", "TodoNext", "TodoCreate", "TodoUpdate"})
"""Every klorb tool name that can change the chainlink task list or the session's current
tracked task."""

_CHAINLINK_DB_RELPATH = Path(".chainlink") / "issues.db"
_GITIGNORE_ENTRY = ".chainlink/"

_LOCK_ERROR_MARKER = "database is locked"
_LOCK_RETRY_ATTEMPTS = 4
_LOCK_RETRY_BASE_DELAY_SECONDS = 0.25
_LOCK_RETRY_JITTER_SECONDS = 0.025

_CHAINLINK_BIN_PATH: Path|None = None
"Path to resolved `chainlink` binary. Cached result from _discover_binary()."


def _vnd_dir() -> Path | None:
    """The repo's `vnd/` vendored-binaries directory, found by walking up from this file's own
    location. `None` outside a source checkout that has one."""
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "vnd"
        if candidate.is_dir():
            return candidate
    return None


def _rust_target_triple() -> str:
    """This machine's Rust target triple, matching the directory names `vnd/Makefile` builds
    chainlink under."""
    machine = platform.machine()
    if platform.system() == "Darwin":
        return f"{'aarch64' if machine == 'arm64' else machine}-apple-darwin"
    return f"{machine}-unknown-linux-gnu"


def _discover_binary() -> Path | None:
    """Return the `chainlink` binary's path: `vnd/<target-triple>/chainlink` in this source
    checkout first, then `$VIRTUAL_ENV/bin/chainlink` if a virtualenv is active, then `PATH`,
    then `$HOME/.cargo/bin/chainlink` as a last resort. `None` if none of these resolve."""

    global _CHAINLINK_BIN_PATH
    if _CHAINLINK_BIN_PATH:
        return _CHAINLINK_BIN_PATH # Cached

    vnd_dir = _vnd_dir()
    if vnd_dir is not None:
        triple = _rust_target_triple()
        vnd_candidate = vnd_dir / triple / "chainlink"
        if vnd_candidate.is_file():
            logger.debug("Found chainlink vendored for %s: %s", triple, vnd_candidate)
            _CHAINLINK_BIN_PATH = vnd_candidate
            return _CHAINLINK_BIN_PATH
        logger.debug("No vendored chainlink for %s at %s.", triple, vnd_candidate)

    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        venv_candidate = Path(virtual_env) / "bin" / "chainlink"
        if venv_candidate.is_file():
            logger.debug("Found chainlink in the active virtualenv: %s", venv_candidate)
            _CHAINLINK_BIN_PATH = venv_candidate
            return _CHAINLINK_BIN_PATH
        logger.debug("chainlink not found in the active virtualenv at %s.", venv_candidate)

    on_path = shutil.which("chainlink")
    if on_path is not None:
        logger.debug("Found chainlink on PATH: %s", on_path)
        _CHAINLINK_BIN_PATH = Path(on_path)
        return _CHAINLINK_BIN_PATH
    logger.debug("chainlink not found on PATH.")

    cargo_candidate = Path.home() / ".cargo" / "bin" / "chainlink"
    if cargo_candidate.is_file():
        logger.debug("Found chainlink at the cargo-install fallback path: %s", cargo_candidate)
        _CHAINLINK_BIN_PATH = cargo_candidate
        return _CHAINLINK_BIN_PATH
    logger.debug("chainlink not found at the cargo-install fallback path %s either.", cargo_candidate)

    return None


def reset_cached_chainlink_path() -> None:
    """Reset cached path so chainlink_available() et al re-scan possible chainlink locations."""
    global _CHAINLINK_BIN_PATH
    _CHAINLINK_BIN_PATH = None


def chainlink_available() -> bool:
    """Return whether the `chainlink` binary can be found."""
    return _discover_binary() is not None


def chainlink_db_exists(workspace_root: Path) -> bool:
    """Whether `.chainlink/issues.db` already exists under `workspace_root`."""
    return (workspace_root / _CHAINLINK_DB_RELPATH).exists()


class ChainlinkError(RuntimeError):
    """Raised when a `chainlink` subprocess call fails: a non-zero exit or the binary can't be
    found. Carries the full command line, working directory, and exit code alongside `chainlink`'s
    own error output."""


def _subprocess_env() -> dict[str, str]:
    """This process's environment, plus `RUST_BACKTRACE=0`."""
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
    """Raise `ValueError` if `priority` isn't one of `PRIORITY_ORDER`'s keys."""
    if priority not in PRIORITY_ORDER:
        raise ValueError(f"priority must be one of {sorted(PRIORITY_ORDER)}, got {priority!r}")


def open_blocker_count(issue: dict[str, Any]) -> int:
    """How many of `issue`'s blockers are still open, per chainlink's `blocked_by_open` field."""
    return len(issue.get("blocked_by_open", []))


def issue_sort_key(issue: dict[str, Any]) -> tuple[int, int, int, int]:
    """The task tools' shared sort order: open before closed, fewest open blockers first, highest
    priority first, then lowest id first."""
    return (
        0 if issue.get("status") == "open" else 1,
        open_blocker_count(issue),
        PRIORITY_ORDER.get(issue.get("priority", "medium"), PRIORITY_ORDER["medium"]),
        issue.get("id", 0),
    )


class ChainlinkClient:
    """Thin wrapper around the `chainlink` CLI: binary discovery, subprocess protocol, and
    lock-contention retry. Every operation is scoped to one root session's issues. Constructing
    without a real `Session` raises `ValueError`."""

    def __init__(self, context: "ToolSetupContext") -> None:
        binary = _discover_binary()
        if binary is None:
            raise ChainlinkError(
                "chainlink binary not found in vnd/, on PATH, in the active virtualenv, or at "
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
        if session.parent is None:
            # Only the root session's own close-time cleanup closes every group issue -- a
            # subagent that happens to construct a ChainlinkClient must not also register this,
            # since every session in a group shares the same group label (see
            # `Session.get_chainlink_label()`) and `cascade_close_subagents` already closes every
            # subagent `Session` before the root's own teardown runs.
            session.register_teardown("ChainlinkClient", self._close_all_on_teardown)

    def _ensure_setup(self) -> None:
        """Run `chainlink init --db-only` and ensure `.chainlink/` is gitignored, but only when
        no `.chainlink/issues.db` exists yet."""
        if (self._workspace_root / _CHAINLINK_DB_RELPATH).exists():
            return
        logger.debug(
            "No chainlink issue database in %s yet; running 'chainlink init --db-only'.",
            self._workspace_root)
        self._run(["init", "--db-only"])
        self._ensure_gitignore_entry()

    def _ensure_gitignore_entry(self) -> None:
        """Add `.chainlink/` to the workspace's top-level `.gitignore`, creating the file if it
        doesn't exist, unless the existing rules already cover it. `issues.db` must never be
        committed."""
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
        """Run `chainlink --json [--quiet] <args>` in the workspace root. Retries up to
        `_LOCK_RETRY_ATTEMPTS` times with exponential backoff (`_LOCK_RETRY_BASE_DELAY_SECONDS`,
        doubling each retry) plus uniform jitter in `[-_LOCK_RETRY_JITTER_SECONDS,
        +_LOCK_RETRY_JITTER_SECONDS]` when chainlink's shared SQLite file is locked by a
        concurrent session. Raises `ChainlinkError` once every attempt fails, or immediately for
        any non-lock-contention failure.
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

    def list_issues(self, *, status: str = "open", extra_label: str | None = None) -> list[dict[str, Any]]:
        """Return every issue under this client's label, additionally ANDed with `extra_label`
        (chainlink's repeatable `--label` filter) when given, in chainlink's own `list --json`
        shape."""
        args = ["issue", "list", "--label", self._label]
        if extra_label is not None:
            args.extend(["--label", extra_label])
        args.extend(["--status", status])
        result = self._run(args)
        parsed: list[dict[str, Any]] = json.loads(result.stdout)
        return parsed

    def show_issue(self, issue_id: int) -> dict[str, Any]:
        """Return one issue's full detail (labels, comments, blocked_by/blocking, etc.)."""
        result = self._run(["issue", "show", str(issue_id)])
        parsed: dict[str, Any] = json.loads(result.stdout)
        return parsed

    def create_issue(
        self, title: str, *, description: str | None = None, priority: Priority = "medium",
        extra_label: str | None = None,
    ) -> int:
        """Create a new issue under this client's label and return its new id.

        `extra_label`, if given, is attached as a second label alongside it.

        `chainlink issue create` doesn't emit JSON regardless of `--json`, so the new id is
        parsed from `--quiet`'s bare-value stdout instead."""
        validate_priority(priority)
        args = ["issue", "create", title, "--priority", priority, "--label", self._label]
        if extra_label is not None:
            args.extend(["--label", extra_label])
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

    def add_label(self, issue_id: int, label: str) -> bool:
        """Add `label` to `issue_id`, returning whether this call actually added it (`True`) as
        opposed to it already being present (`False`)."""
        result = self._run(["issue", "label", str(issue_id), label], quiet=True)
        return "Added" in result.stdout

    def remove_label(self, issue_id: int, label: str) -> bool:
        """Remove `label` from `issue_id`, returning whether this call actually removed it
        (`True`) as opposed to it already being absent (`False`)."""
        result = self._run(["issue", "unlabel", str(issue_id), label], quiet=True)
        return "Removed" in result.stdout

    def close_all(self) -> None:
        """Close every open issue under this client's label. Always passes
        `--no-changelog`."""
        self._run(["issue", "close-all", "--label", self._label, "--no-changelog"], quiet=True)

    def _close_all_on_teardown(self) -> None:
        """`Session.register_teardown` callback: close every remaining open issue under this
        label when the session ends. Logged and swallowed rather than raised: `Session.close()`
        runs every registered teardown unconditionally and must not be interrupted by one of
        them failing."""
        try:
            self.close_all()
        except ChainlinkError:
            logger.warning(
                "Failed to close-all chainlink issues for label %r on session close.",
                self._label, exc_info=True)

    def fetch_and_sort_issues(
        self, *, include_closed: bool, extra_label: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch every issue under this client's label (optionally ANDed with `extra_label`;
        `--status all` if `include_closed`, else just `--status open`), enrich each with its full
        `show_issue` detail, and sort per `issue_sort_key`."""
        base = self.list_issues(status="all" if include_closed else "open", extra_label=extra_label)
        issues = list(map(lambda entry: self.show_issue(entry["id"]), base))
        issues.sort(key=issue_sort_key)
        return issues
