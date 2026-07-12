# Tee Textual's unhandled-exception dump to a /tmp crash log, not just stderr

* Date: 2026-07-12
* Question: When the REPL hits an unhandled exception, Textual's own crash handling
  (`App._handle_exception` -> `App._fatal_error`/`panic` -> `App._print_error_renderables`)
  restores the terminal and prints a full `rich.traceback.Traceback` (with locals) to
  `App.error_console`, which defaults to `stderr`, on its way out — it never raises the
  exception back out of `App.run()`. That dump is large and scrolls past in a terminal that's
  often about to be resized/cleared by whatever shell prompt or wrapper regains control, so
  the most useful diagnostic klorb ever produces for a crash is easy to lose. Should klorb also
  capture that output to a file, and if so, where and how?
* Answer: `klorb.logging_config.run_repl()`'s caller installs a `CrashLogTee` (`klorb.
  logging_config.CrashLogTee`) as `ReplApp.error_console.file` before calling `App.run()`.
  `CrashLogTee` is a `typing.TextIO` (a real subclass, not a duck-typed object needing a
  `cast`) that duplicates every `write()` to both the real stream (`sys.stderr`) and a crash
  log file, opened lazily on the first write so a session that never crashes never creates
  one. The file lives in the OS temp directory (`tempfile.gettempdir()`), not
  `KLORB_STATE_DIR`/`SESSION_LOGS_DIR`, named `klorb-crash-<workspace basename>-<timestamp>.log`
  (`crash_log_path()`). After `App.run()` returns, `run_repl()` checks `app.return_code` —
  `_handle_exception` sets it to `1`, a plain `App.exit()` leaves it `0` — and if it's `1`,
  prints a short pointer line to stderr naming the file (or says it couldn't be written, if
  `CrashLogTee.opened_log_path()` is `None`). `run_repl()` calls `crash_tee.close()` in a
  `finally` block regardless of outcome, which closes the crash log file handle (if one was
  ever opened) without touching `stream` — `CrashLogTee` doesn't own that.
* Reasoning: `Console.file` is a public, documented extension point (a plain setter), and
  `error_console` is used for nothing except this one crash-dump print, so replacing it is a
  narrow, low-risk hook rather than needing to fork Textual's exception handling or wrap
  `App.run()` in a `try`/`except` that would never fire (Textual swallows the exception
  itself). Duplicating writes rather than replacing the destination keeps the existing
  in-terminal crash experience unchanged — the same text still lands on stderr — while adding
  a durable copy. `/tmp` was chosen over `KLORB_STATE_DIR` because a crash dump is a one-off
  diagnostic artifact to hand to a bug report, not project state klorb needs to read back
  later (no `schema: {name, version}` envelope applies here — it's plain text, not JSON), and
  `/tmp` is where a user or CI system already expects debug artifacts to accumulate and get
  cleaned up automatically. Opening the file lazily (on first write, not in `CrashLogTee.
  __init__`) means a normal, crash-free session never litters `/tmp`. Writing to the file
  before attempting the stream write, and catching `OSError` around each independently, means
  a failure on one side (a full disk, a broken pipe on stderr) doesn't cost the other — the
  log file is the priority if only one can succeed.

  `CrashLogTee` subclasses `typing.TextIO` directly (implementing every abstract member —
  `read`/`readline`/`readlines`/`seek`/`tell`/`truncate`/`fileno` raise `io.
  UnsupportedOperation`, `readable`/`seekable` report `False` — since Rich's `Console` never
  calls them on `.file`) rather than the more common `io.TextIOBase`, because typeshed's
  `io.*` hierarchy and `typing.IO`/`TextIO` are two unrelated class hierarchies: a `io.
  TextIOBase` subclass is not nominally an `IO[str]`/`TextIO` to mypy no matter what it
  implements, so assigning it to `Console.file` (typed `IO[str]`) would still need a `cast`.
  Inheriting from `typing.TextIO` itself is the only way to make that assignment type-check
  without one. `close()` closes the crash log file handle it opened (mirroring a normal file's
  `close()`) but deliberately leaves `stream` open, since `CrashLogTee` is hard-coded to be
  handed a caller-owned stream (`sys.stderr` in production, a test double in tests) it never
  created and has no business tearing down.
