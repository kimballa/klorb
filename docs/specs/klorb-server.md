# `klorb server`

## Summary

`klorb server` runs klorb as a persistent, non-interactive process that speaks
newline-delimited JSON (JSONL) over stdin/stdout, for driving klorb from another program (a
supervisor process, an IDE extension, a test harness) rather than a terminal. It's reachable as
a CLI subcommand (`klorb server`) with no flags of its own today.

## Wire protocol

* Input is read from stdin one line at a time. Each line is a single JSON value ("JSONL");
  leading/trailing whitespace on the line is stripped before parsing. A blank line (empty after
  stripping) is skipped rather than treated as a record.
* A JSON value must not contain a literal, unescaped newline — a multi-line string is not
  representable; a newline within a string value must be the two-character escape `\n`, exactly
  as `json.dumps` already produces it. This is what makes line-buffered reads unambiguous: the
  first unescaped `\n` always ends the current record.
* Every reply is written to stdout as one JSON value serialized on a single line (`json.dumps`,
  which never emits an embedded literal newline) followed by `\n`, and flushed immediately —
  a caller reading stdout sees each reply as soon as it's produced, without waiting for a
  buffer to fill.
* Every input record is a JSON object (not a bare string/number/array); a non-object record, or
  a line that fails to parse as JSON at all, gets an `{"error": "..."}` reply rather than
  stopping the server.

### Recognized commands

* `{"greet": "someName"}` → replies `{"message": "hello, someName!"}`. `"greet"`'s value must
  be a string; otherwise the reply is `{"error": "'greet' must be a string"}`.
* `{"action": "shutdown"}` → stops the read loop (no reply is written for this command itself)
  and the process exits with status 0. Any further input after a shutdown command is on the
  same line-buffered stream but is never read — the loop returns as soon as this command is
  dispatched.
* Any other object shape gets `{"error": "unrecognized command"}` — a forward-compatible
  fallback so a client can distinguish "this klorb version doesn't know this command yet" from
  a malformed request.
* Stdin reaching EOF (the pipe closes without a shutdown command) also stops the read loop, the
  same as an explicit shutdown — the process exits with status 0 either way.

## How it works

* `klorb.server` (`klorb/src/klorb/server/`) holds the library logic, independent of the CLI:
  * `klorb.server.jsonl_server.JsonlServer` (re-exported as `klorb.server.JsonlServer`) is
    constructed with the `stdin`/`stdout` text streams to read from and write to
    (`JsonlServer(stdin=..., stdout=...)`) rather than reaching for `sys.stdin`/`sys.stdout`
    itself, so a test can drive it against `io.StringIO()` instead of real file descriptors.
  * `run() -> int` loops `for line in self._stdin`, dispatches each non-blank line via
    `_handle_line()`, and returns `0` once the loop ends — either because `_handle_line()`
    signaled a shutdown command (returns `False`) or because stdin reached EOF (the `for` loop
    ends on its own). There is currently no error condition that produces a non-zero return
    from `run()`; a malformed or unrecognized record is reported to the caller as an
    `{"error": ...}` reply on stdout, not as a process failure.
  * `JsonlServer` installs no `SIGINT` handling of its own — see "SIGINT handling" below.
* `klorb.cli.run_server_cli(argv)` parses `klorb server`'s own flags (none today, beyond the
  free `-h`/`--help` argparse provides), constructs a `JsonlServer` against the process's real
  `sys.stdin`/`sys.stdout`, and calls `run()`. `klorb.cli.main()` recognizes `klorb server ...`
  the same way it recognizes the other subcommands (`init`, `system-prompt`, `models`,
  `show-config`): only when `server` is literally `sys.argv[1]`, checked before the normal
  one-shot/REPL `argparse` parser runs, so it can't be confused with `server` appearing later
  in `argv` (e.g. as a one-shot prompt's own text).

### SIGINT handling

`klorb server` deliberately does not install a custom `SIGINT` handler, unlike
`klorb.tools.bash.BashTool`'s persistent shell or the REPL's own interrupt/liveness watchdog
(see [[interrupt-and-liveness-watchdog]]) — this is a plain, single-threaded, synchronous
read-dispatch-reply loop with no subprocess or long-running turn to cancel out from under. A
`SIGINT` (Ctrl-C, or an external `kill -INT`) delivered while `JsonlServer.run()` is blocked
reading stdin is left to the interpreter's ordinary `KeyboardInterrupt`, the same as it would
be for any other unadorned Python script: it unwinds the blocked read and propagates out of
`run()`. `klorb.cli.run_server_cli()` catches `KeyboardInterrupt` at that one point purely to
exit with status 0 instead of letting a traceback print to stderr — it does not change what
signal disposition is in effect while the loop is running.

## Usage

```bash
klorb server
```

```text
> {"greet": "Ada"}
< {"message": "hello, Ada!"}
> {"unknown": "command"}
< {"error": "unrecognized command"}
> {"action": "shutdown"}
(process exits with status 0)
```

## Out of scope

* No commands beyond `greet` and `action: shutdown` exist yet. The `{"error": "unrecognized
  command"}` fallback for any other object shape is what makes adding new commands later
  backward-compatible: an older client sending an unknown command already gets a defined,
  parseable reply instead of the server hanging or crashing.
* There is no concurrent/multiplexed request handling — one line in, one reply out, in order.
  A caller that needs concurrent in-flight requests would need to correlate them itself (e.g.
  a request id echoed back in each reply), which isn't part of the protocol today.
