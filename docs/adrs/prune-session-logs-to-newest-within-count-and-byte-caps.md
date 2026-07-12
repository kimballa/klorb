# Prune session logs to the newest files within both a count cap and a byte cap

* Date: 2026-07-11 12:00
* Question: The `session-logs/` directory grew without bound — every run left its log file in
  place forever (see the "Out of scope" note that used to be in
  docs/specs/paths-and-logging.md). How should klorb bound that directory, and when?
* Answer: When a new session log is opened (`configure_logging()` with a `log_path`, which
  happens at process start and again on the REPL's `/clear`), first prune the directory via
  `prune_session_logs()`. It keeps the most-recently-*modified* `*.log` files subject to two
  hard caps applied together — at most `DEFAULT_MAX_SESSION_LOG_FILES` (12) files and at most
  `DEFAULT_MAX_SESSION_LOG_BYTES` (32 MiB) total — deleting the rest. The file about to be
  opened is reserved one slot against the file-count cap and is never deleted. The single
  newest existing log is always retained regardless of its size (so a lone over-cap log is
  kept, not erased); older logs are kept only while they still fit under the byte cap.
* Reasoning:
  * **Prune at open, not on a timer or a background thread.** klorb has no long-running
    scheduler, and the only moment a new file appears is `configure_logging()`; doing the
    cleanup right there keeps it deterministic, testable, and free of any concurrency
    machinery. It matches the sibling decision that `/clear` starts a new session and log
    file ([[clear-command-starts-a-new-session-and-log-file]]).
  * **Recency by mtime, not by the timestamp embedded in the filename.** A long-running
    session keeps writing to its file, so its mtime stays fresh even though its name encodes
    the (older) start minute. mtime is the honest "most recently useful" signal; the coolname
    filename ([[log-via-textualhandler-and-session-file-with-coolname-nonce]]) is only for
    human reference and same-minute disambiguation.
  * **Both caps are hard caps, evaluated together (the stricter wins).** A pure file-count cap
    lets a handful of enormous logs blow past any disk budget; a pure byte cap lets thousands
    of tiny logs pile up. Requiring both bounds simultaneously covers each other's failure mode.
  * **Reserve a slot for the incoming file** so that "at most N files" holds *after* the new
    file is created, giving a stable steady state of exactly N files rather than N+1.
  * **Never delete below one log "of any length".** The byte cap must not be able to wipe the
    directory clean: if the newest log alone exceeds the byte cap, deleting it would throw away
    the very session a user is most likely to want to inspect. So the newest existing file is
    retained unconditionally, and the byte cap only governs whether *older* files survive.
  * **Caps are module constants, not config keys, for now.** The backlog item only asked for
    sane default bounds; wiring `ProcessConfig`/`PROCESS_KEY_MAP` keys is a mechanical follow-up
    ([[process-and-session-config]]) and was left out to keep this change small. The production
    callers pass neither argument, so they get the defaults.
