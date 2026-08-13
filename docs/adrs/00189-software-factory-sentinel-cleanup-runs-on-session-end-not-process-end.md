# Software-factory sentinel cleanup fires on `onSessionEnd`, gated by a `root_session_id` latch

* Date: 2026-08-13
* Question: `docs/plans/auto/`'s software-factory sentinels (`.enable_software_factory.tmp`,
  `.factory_in_progress.tmp`) carried no identity — any session's `onAgentTurnEnd`/
  `FileSystemModified` firing treated the mere *presence* of the enable sentinel as "mine to
  manage." Cleanup (`disable_sentinel.sh`) was wired to `onProcessEnd`, which never fires at all
  for `klorb server` (its only dispatch site is `cli.py`'s interactive/one-shot subcommand,
  unreachable from `run_server_cli`) — so a server process serving several sessions in sequence
  (`session/new`/`session/load`, each replacing the prior live session) never cleaned up between
  them. With `Session.id`/`root_id` now fixed for a session's whole life (see ADR 00188), and a
  workspace able to have more than one klorb process open against it at once, how should sentinel
  ownership and cleanup timing work?
* Answer: `.enable_software_factory.tmp`'s content becomes the `root_session_id` of the session
  that turned the mode on (written by `enable_sentinel.py`'s `onActivateSkill` handler, which
  already receives `root_session_id` on its `HookInput`), not an empty touch file. Every other
  factory hook — `on_turn_end.py`, `on_file_changed.py`, `disable_sentinel.py` — reads that
  content via `queue_utils.read_latch_owner()` and treats a missing or mismatched owner as "not
  mine to manage": a silent no-op, not a nudge or a termination-log line. `disable_sentinel.py`
  (renamed from `.sh`, now needing real JSON parsing) is rewired from `onProcessEnd` to
  `onSessionEnd`, filtered in `.klorb/klorb-config.json` to `reason: "SuspendSession"` only —
  `onSessionEnd` also fires with `reason: "ResetSession"` on every one of the loop's own restart
  cycles (`on_turn_end.py`'s `reset_session: true` output), and unconditionally tearing the mode
  down on *that* firing would break the very restart loop it's supposed to support. It only
  actually removes the sentinels when its own `root_session_id` matches the latch's recorded
  owner. `enable_sentinel.sh` is converted to `enable_sentinel.py` for the same reason
  `disable_sentinel.py` is — real JSON parsing beats hand-rolled bash. `.factory_in_progress.tmp`
  stays a content-free touch file, created/removed directly by the `enable-software-factory`
  skill's own bash commands (unchanged): it's meaningless without the enable sentinel, so
  ownership only needs to be tracked once, on the file a *hook* (which already has
  `root_session_id` on hand) controls.
* Reasoning: Fixing `Session.id` for a session's whole life (ADR 00188) is what makes a stable
  `root_session_id`-based latch workable in the first place — under the old rename-in-place
  design, a latch written with a pre-rename id would stop matching partway through the very
  session that wrote it. Gating cleanup on `onSessionEnd` rather than `onProcessEnd` is a strictly
  more correct scope for "this session's own footprint" regardless of how many sessions a single
  process serves; the `SuspendSession`-only filter is necessary precisely because the factory
  loop's normal operation already produces `onSessionEnd` firings that must *not* trigger cleanup.
  Two sessions racing to *enable* the mode at the same moment (the second `enable_sentinel.py`
  overwrites the first's latch) is a real remaining gap, but solving it needs actual cross-process
  coordination (a `Lockfile`, not a plain file) — out of scope here, and no worse than the
  pre-existing "last write wins" behavior of a bare presence check. A hard process crash still
  strands the sentinels (no `onSessionEnd` fires either), unchanged from what `onProcessEnd`
  already couldn't guarantee against a `kill -9`.
