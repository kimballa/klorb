# Session naming runs inside `Session`, not the TUI; `Session.set_id()` owns id/root_id

* Date: 2026-07-21
* Question: The session-naming classifier (deriving a friendly title/slug from a session's first
  prompt) ran only from `klorb.tui.mixins.prompt_submission.PromptSubmissionMixin.
  _run_session_naming`, which reassigned `self._session.id = new_id` directly. `ChainlinkClient`
  labels every chainlink issue with `session.get_chainlink_label()`, which returns `root_id`, not
  `id` — but nothing ever renamed `root_id`, so a renamed session's chainlink tasks stayed
  labeled with its original random-nonce id forever. Separately, naming was TUI-only: a headless
  one-shot (`-m`/`--message`) session never got a friendly name at all. Where should naming run,
  and who should own mutating `id`/`root_id`?
* Answer: Naming moved into `Session.send_turn()` itself, as a fourth one-shot block alongside
  the pre-existing `_skills_seeded`/`_context_files_seeded`/`_metadata_seeded` interjection
  blocks — so it runs identically on the first `send_turn()` call regardless of whether that
  call came from the interactive TUI or `Session.run_one_shot()` (headless). `Session.set_id()`
  is now the only sanctioned way to change `session.id`: it updates `root_id` to match whenever
  `id == root_id` (true for every session today, since klorb has no subagent-spawning mechanism
  yet), and leaves `root_id` alone otherwise. The naming classifier calls `set_id()` instead of
  assigning `self.id` directly. A new `TurnEventHandlers.on_session_name_changed` callback fires
  once, synchronously, right after naming is attempted — the TUI reacts to it to update its
  status line and (if session logging is enabled) rename the session log file; a headless caller
  passes no callback and simply gets a renamed `Session` with nothing reacting to it. No new
  threading was introduced: the classifier call runs on whatever thread already calls
  `send_turn()` (the TUI's existing `@work(thread=True)` worker thread, or headless's single
  thread), and only the TUI's reaction callback hops back onto the UI thread via
  `call_from_thread`, exactly as every other `TurnEventHandlers` callback already does.
* Reasoning: `id`/`root_id` are both owned by `Session` (`klorb.session.mixins.core.
  SessionCoreMixin`), so the invariant that keeps them in sync belongs there too, not in a TUI
  mixin that only ever saw `id`. Moving the classifier call itself into `Session.send_turn()`
  was the natural way to get headless parity for free, rather than duplicating the
  trigger/pending-flag logic in `cli.py` — `send_turn()` already has an established "first call
  on this instance" pattern for exactly this shape of one-shot work. Reacting via
  `TurnEventHandlers.on_session_name_changed` reuses the same idiom every other "`Session` does
  something on a worker thread, caller updates UI in response" case already uses in this
  codebase, rather than inventing a new notification mechanism. Session log-file renaming was
  deliberately kept TUI-only rather than extended to headless: headless has no existing
  per-session log-file concept (`_session_log_enabled` is a TUI/`ReplApp` attribute with no
  `Session`/`ProcessConfig` equivalent), and adding one was out of scope for what was otherwise a
  bugfix plus a headless-parity change. This was surfaced while investigating why
  `TodoCreate`-created chainlink issues kept the session's original random-nonce label even after
  the session was renamed to a friendly slug — see docs/specs/chainlink-task-tracking.md's
  `get_chainlink_label()` documentation and docs/specs/session-and-turns.md's "Session naming"
  section for the resulting behavior.
