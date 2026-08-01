# Use the `watchdog` package for the TUI file finder's workspace index

* Date: 2026-08-01
* Question: The TUI's `@`-mention fuzzy file finder (see
  [[at-mention-file-inlining]]'s "Interactive fuzzy finder" section) needs a candidate file
  list that stays current as files are created or deleted while klorb runs -- mirroring the VS
  Code plugin's own `vscode.workspace.createFileSystemWatcher`-based index
  (`vscode-plugin/src/host/features/fileSearch/fileSearch.ts`). Python has no built-in
  equivalent; should `klorb.tui.workspace_file_index.WorkspaceFileIndex` poll the workspace on
  a timer, or use real filesystem push notifications, and if the latter, through what library?
  And once notified, should every event trigger a full rescan, or should some be applied
  incrementally?
* Answer: Use the `watchdog` PyPI package (added as a new runtime dependency,
  `watchdog >= 6.0.0, < 7.0.0`) -- its `Observer`/`FileSystemEventHandler` wrap the OS-native
  notification API on each platform (inotify on Linux, FSEvents on macOS,
  `ReadDirectoryChangesW` on Windows), the same mechanism WSGI dev-server reloaders (e.g.
  werkzeug's) use to detect source changes without polling. A plain file's own creation or
  deletion is applied as a single incremental add/remove against the cached list; a directory
  create/delete or any change to a `.gitignore` file forces a full rescan instead. Every update
  (incremental or full) is debounced by 400ms so a burst of events (an `npm install`, a branch
  checkout) collapses into one refresh. `watchdog` is unrelated to `klorb.watchdog.
  LivenessWatchdog`, klorb's own hang-detection heartbeat -- the name collision is coincidental.
* Reasoning: A polling loop (`os.walk` every N seconds) either wastes CPU rescanning an idle
  workspace or lags behind real changes depending on the interval chosen, and either way adds a
  background timer that never sleeps. `watchdog` delivers events immediately, at effectively
  zero idle cost, and is the de facto standard for this in Python -- it's what Flask/werkzeug's
  own reloader is built on, which is precisely the "how do WSGI dev servers do this" question
  the feature's own design discussion raised. It was not yet a klorb dependency, but adding one
  is a normal, self-contained change (`pyproject.toml` plus `make sync_deps`), not a reason to
  prefer polling.

  Folding a directory create/delete into a full rescan, rather than diffing it against the
  cached list the way a single file's own event is handled, is a deliberate simplification: a
  removed directory deletes every file beneath it in one filesystem event, and correctly
  removing just those entries would mean walking the cached list for a matching path prefix (or
  re-deriving the directory's prior contents) for every such event. The same is true for a
  `.gitignore` change, which can alter what's excluded across an entire subtree and can't be
  applied by touching one path -- `pathspec`'s `GitIgnoreSpec` has no way to "un-apply" rules
  already folded into a `GitignoreFilter`. Directory operations and `.gitignore` edits are rare
  next to individual file creates/deletes during ordinary editing, so paying a full rescan's
  cost only for those events keeps the implementation (and its test suite) far simpler while
  still avoiding a rescan for the overwhelmingly common case.
* Alternatives rejected:
  * Polling on a fixed interval: simplest to implement, but forces a choice between wasted CPU
    (short interval) and perceptible staleness (long interval) with no good default; rejected
    in favor of push notifications, which have neither failure mode.
  * Full incremental diffing for every event kind, including directory bulk operations and
    `.gitignore` changes (mirroring the VS Code plugin's own per-event granularity as closely
    as possible): rejected for this first version as unnecessary complexity -- a full rescan on
    those specific, comparatively rare events is a strict correctness superset of diffing them,
    at a small, infrequent CPU cost.
