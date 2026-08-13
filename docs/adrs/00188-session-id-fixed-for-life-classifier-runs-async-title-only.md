# Session id is fixed for life; the naming classifier runs async and derives a title only

* Date: 2026-08-13
* Question: `docs/adrs/00149-session-naming-runs-inside-session-not-the-tui.md` made
  `Session.set_id()` the only sanctioned way to rename `Session.id`/`root_id` in place, driven by
  the session-naming classifier's derived `slug`. That rename forced three pieces of accidental
  complexity: the TUI had to close and reopen its session log file when the rename landed; the
  ACP server couldn't treat `Session.id` as the client-facing session id, so it snapshotted a
  second `KlorbAcpAgent._acp_session_id` and was careful never to read `Session.id` for that
  purpose again; and the classifier had to run *synchronously*, blocking the first turn's
  dispatch, since nothing could be persisted to a session-id-keyed directory until the id's
  "final form" was known. Should `Session.id` keep being renamed, or should it be fixed for the
  session's whole life instead?
* Answer: `Session.id`/`Session.root_id` are set once in `Session.__init__` and never reassigned
  again — `SessionCoreMixin.set_id()` is deleted, along with `Session.aliases` and its
  `sessions.json`/`session.json` persistence (`RecentSession.aliases`/`SessionState.aliases`,
  `find_recent_session`'s alias-matching branch), all of which existed only to let a stale
  pre-rename id keep resolving. `klorb.session_naming.SessionName` drops its `slug` field
  entirely — the classifier only ever derives a `title`, which `_run_session_naming` returns
  without touching `Session` state at all (it's now a pure resolver).
  `SessionCoreMixin._start_session_naming` kicks the classifier off on a background daemon
  thread and returns immediately, so `send_turn()`'s own dispatch is never blocked by its round
  trip — a fresh sentinel token guards against a superseded result (a later call, a user-driven
  `_klorb/setSessionTitle` rename via `cancel_session_naming()`, or session teardown/reset)
  overwriting a name it no longer has authority over, and `close()`'s teardown joins the thread
  bounded by the classifier's own end-to-end timeout so a nearly-finished call still lands its
  result (this matters most for a headless one-shot run, whose process exits right after
  `close()`). `SessionPersistenceMixin.claim_session_directory()` moves to run synchronously,
  before the classifier thread is spawned, rather than after naming resolves — `self.id` is
  already final at that point, so there's no more reason to wait. `KlorbAcpAgent` drops
  `_acp_session_id` and reads `self._session.id` directly throughout; `build_subagent_tree_snapshot`
  drops its now-pointless root-id override for the same reason. `TurnBridge`'s
  `on_session_name_changed` can no longer deliver its `session_info_update` through the
  per-turn-iteration update queue (the classifier can resolve after that iteration's queue pump
  has already stopped), so it schedules the notification directly via `asyncio.
  run_coroutine_threadsafe` instead. The TUI's `GettingReadyStatic` "getting ready" widget and
  its log-file-reopen dance in `_handle_session_name_changed` are both deleted outright: with the
  real turn dispatching immediately regardless of naming, there is no more "blocked on the
  classifier" phase for `GettingReadyStatic` to represent, and the log path (`session_log_path
  (session.id)`) never needs to move again.
* Reasoning: All three pieces of accidental complexity this ADR removes existed to accommodate a
  single fact: `Session.id` could change mid-session. Fixing identity at construction removes the
  need for each of them individually, rather than patching each one separately. Making the
  classifier asynchronous was only *safe* to do once id-rename was gone — with a mutable id, a
  turn's own directory claim couldn't safely race the classifier's rename; with a fixed id, the
  two are fully independent and the classifier's own latency stops taxing the first turn's
  time-to-first-token, per the user's own request that prompted this change. This supersedes
  docs/adrs/00149-session-naming-runs-inside-session-not-the-tui.md's "`Session.set_id()` is the
  only sanctioned way to change `session.id`" answer; that ADR's other conclusion (naming runs
  inside `Session.send_turn()` itself, for TUI/headless parity) still stands unchanged.
