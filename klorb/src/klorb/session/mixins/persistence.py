# © Copyright 2026 Aaron Kimball
"""`SessionPersistenceMixin`: `Session`'s ownership of its own saved state and `session.lock` --
claiming a `sessions/<subdir>/` directory in its workspace's per-project data directory, writing
`session.json` to it, and keeping `sessions.json`'s recency index current. See
docs/specs/session-persistence.md and docs/plans/ready/plan-017-multiple-sessions-per-workspace.md.

This mixin, not the TUI or the ACP server, decides *whether* and *when* a session's state hits
disk -- every caller (`klorb.tui.ReplApp`, a headless one-shot run, `klorb.server.klorb_agent.
KlorbAcpAgent`) just calls `persist_state()`/`close()` at the right moments (end of turn, quit,
crash, an explicit "clear session"/"start a new session"), so the on-disk format and locking
discipline can't drift between the three call sites the way three independent implementations
of "write `last-session.json`" once could have.

There is deliberately no `atexit`-registered backstop that calls `close()` on interpreter
shutdown: every real entry point already calls `close()` (or `persist_state()`) explicitly on
its own way out -- the TUI's `action_quit`, its crash/force-exit paths, `klorb.cli.main()`'s
one-shot path, and `KlorbAcpAgent.close()` -- so an `atexit` hook would be pure redundancy for
production use, while for tests (which construct many short-lived `Session`s, most never
explicitly closed) it would both leak every claimed `Session` for the rest of the process and,
worse, fire its writes at interpreter shutdown *after* `pytest`'s `monkeypatch` fixtures have
already reverted any per-test filesystem isolation -- writing real session files into the
developer's actual `$KLORB_DATA_DIR`.
"""

import logging
from datetime import datetime

from klorb.lockfile import Lockfile, create_lockfile
from klorb.session.mixins._base import SessionBase
from klorb.workspace.session_store import session_lock_path, touch_recent_session, write_session_state

logger = logging.getLogger(__name__)


class SessionPersistenceMixin(SessionBase):
    """Claims a `sessions/<subdir>/` directory for this `Session` (once its workspace is
    trusted and its id has stopped changing -- see `claim_session_directory`), and keeps
    `session.json`/`sessions.json` current for as long as this `Session` stays open."""

    def claim_session_directory(self) -> None:
        """Take ownership of `sessions/<self.id>/` in this session's workspace: acquire
        `session.lock` there (one-shot, never retried -- a locked directory means another live
        process already owns it, not "wait for it to free up"), write the initial
        `session.json`, and record the resulting recency entry in `sessions.json`. A no-op if
        already claimed, or if `config.workspace.trusted` is `False` -- the same trust gate the
        input-history store and this module's predecessor (`klorb.workspace.last_session`)
        always applied; an untrusted or unresolved workspace is never written to.

        Called once per `Session`, from `SessionTurnsMixin.send_turn()` right after session
        naming has resolved on the first turn (whether the classifier renamed `self.id`, fell
        back to `klorb.session_naming.fallback_session_title`, or was already skipped because
        this is a restored session) -- data must never land in a session-id-specific directory
        before the id's final form is decided. Every later `persist_state()` call also calls
        this first, as a no-op once already claimed, so it's safe to call from more than one
        place.

        On losing the one-shot lock race (vanishingly unlikely, given `Session.id`'s
        timestamp+nonce uniqueness, but possible), logs a warning and leaves this session
        unclaimed: every future `persist_state()` call becomes a no-op rather than raising and
        interrupting whatever turn triggered it.
        """
        if self._session_claimed:
            return
        if not self.config.workspace.trusted:
            return
        subdir = self.id
        lock = create_lockfile(session_lock_path(self.config.workspace, subdir))
        if not lock.try_acquire():
            logger.warning(
                "Could not claim session directory sessions/%s for session %s (already "
                "locked by another process); this session will not be persisted.",
                subdir, self.id)
            return
        self._session_lock = lock
        self._session_subdir = subdir
        self._session_claimed = True
        logger.debug("Claimed session directory sessions/%s for session %s.", subdir, self.id)
        self._write_session_state_and_touch()

    def adopt_claimed_session_directory(self, subdir: str, lock: Lockfile) -> None:
        """Take ownership of an already-acquired `session.lock` for `sessions/<subdir>/`, for a
        session being restored from a previously saved `session.json` -- `subdir` and `lock`
        come from the caller's own successful lock-acquisition attempt against a `sessions.json`
        entry (see `klorb.tui.mixins.workspace_bootstrap.WorkspaceBootstrapMixin.
        _maybe_restore_latest_session` and `klorb.server.klorb_agent.KlorbAcpAgent.
        load_session`), *before* this `Session` is even constructed.

        Unlike `claim_session_directory`, this never derives `subdir` from `self.id` or acquires
        a fresh lock itself: a restored session's on-disk subdirectory can differ from its
        (possibly since-renamed) `self.id` (see `klorb.workspace.session_store.RecentSession.
        subdir`), so the caller -- which already resolved the right subdirectory from
        `sessions.json` before deciding whether the restore could proceed at all -- must hand
        both through directly rather than have this method re-derive (and potentially get
        wrong) them.
        """
        self._session_lock = lock
        self._session_subdir = subdir
        self._session_claimed = True

    def persist_state(self) -> None:
        """Write this session's current `session.json` and refresh its `sessions.json` recency
        entry -- claiming its directory first if this is the first call (see
        `claim_session_directory`). A no-op if the workspace is untrusted (claiming never
        succeeds) or the one-shot lock was already lost.

        Called at the end of every turn (success, error, or interruption alike -- see
        `klorb.tui.mixins.prompt_submission.PromptSubmissionMixin._finish_turn` and
        `klorb.server.klorb_agent.KlorbAcpAgent.prompt`) and by every process-exit path that
        can't cleanly call `close()` (the TUI's crash/force-exit paths, which deliberately leave
        `session.lock` held rather than releasing it -- see those callers' own docstrings)."""
        self.claim_session_directory()
        if not self._session_claimed:
            return
        self._write_session_state_and_touch()

    def _write_session_state_and_touch(self) -> None:
        assert self._session_subdir is not None
        self._last_modified_at = datetime.now()
        write_session_state(
            self.config.workspace, self._session_subdir, self.config, self._messages,
            statistics=self.statistics, session_id=self.id, root_id=self.root_id,
            session_name=self._session_name, cur_chainlink_task_id=self.cur_chainlink_task_id,
            last_modified_timestamp=self._last_modified_at)
        touch_recent_session(
            self.config.workspace, self.id, self._session_subdir, self._session_name,
            last_modified_timestamp=self._last_modified_at)

    def _finalize_session_persistence(self) -> None:
        """Write a final `session.json` and release `session.lock` -- called from
        `SessionCoreMixin.close()` before it runs teardown callbacks, so "close" always means
        "persist one last time, then let go of the lock," per this session's design notes. A
        no-op if this session was never claimed (an untrusted workspace, or a session that never
        survived past its first turn's naming step). Idempotent, matching `close()`'s own
        idempotency contract -- a second call finds `_session_claimed` already `False` and does
        nothing.
        """
        if not self._session_claimed:
            return
        self._write_session_state_and_touch()
        assert self._session_lock is not None
        self._session_lock.release()
        self._session_lock = None
        self._session_subdir = None
        self._session_claimed = False
