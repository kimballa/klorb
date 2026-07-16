# Interrupt handling and the liveness watchdog

## Why this exists

klorb's TUI runs on Textual, which puts the terminal into raw mode with the `ISIG` termios
flag cleared (Textual's `LinuxDriver`, unless `TEXTUAL_ALLOW_SIGNALS` is set). With `ISIG`
off, typing `Ctrl+C` does **not** generate a `SIGINT`: the raw `0x03` byte is delivered to
stdin, read by Textual's input reader, and dispatched as a `ctrl+c` **key event**. So while
the TUI owns the terminal, a process-level `signal.signal(SIGINT, ...)` handler never fires
from a keyboard `Ctrl+C` — the only real `SIGINT` a running TUI can receive is an external
`kill -INT`. See docs/adrs/remove-sigint-handler-because-textual-disables-isig.md.

That left klorb with no reliable last-ditch escape when a turn wedged. This feature adds three
cooperating mechanisms so a stuck klorb can always be recovered without an external `kill`, and
so the user always gets feedback that an interrupt was received.

## The two hang modes

A turn runs on a Textual thread worker (`ReplApp._send_prompt` / `_run_shell_command`), while
the event loop runs on the main thread. Two distinct things can wedge:

1. **Worker stuck, event loop alive.** The worker thread is blocked inside `Session.send_turn`
   — a tool that doesn't observe `cancel_event`, a `call_from_thread` target, or an interaction
   future that never resolves. `Session.send_turn` never returns, so the worker never reaches
   `ReplApp._finish_turn`, so `_turn_in_flight` stays `True` forever: new submits are dropped,
   `Escape`/`Ctrl+C` set a `cancel_event` nobody reads, and a requested quit is deferred behind
   a worker that never unwinds (`_begin_exit`). The event loop itself is still healthy and
   processing key events. This is the common case.
2. **Event loop itself wedged.** A `call_from_thread` UI target (or another synchronous handler)
   blocks the main thread. Key events — including `Ctrl+C` — queue up unprocessed; `action_*`
   handlers never run at all. Only a thread that isn't the main thread can escape this.

## The three mechanisms

### 1. Interrupt feedback: "Interrupting…"

`action_abort_response` (Escape) and `action_interrupt` (Ctrl+C, while a turn is in flight)
mount a one-line `Interrupting…` notice into the history the first time they're pressed during a
turn (`ReplApp._note_interrupt_requested`, reset per turn in `_finish_turn`). This runs on the
event loop, so in hang mode (1) the user gets immediate confirmation the keystroke was received
and the app isn't silently ignoring them — the difference between "it's working on stopping" and
"it's deadlocked". The Ctrl+C notice also tells the user that a second Ctrl+C forces a quit.

### 2. Double-Ctrl+C force-quit (hang mode 1)

`action_interrupt` records the timestamp of each Ctrl+C. A second Ctrl+C within
`_DOUBLE_INTERRUPT_WINDOW_SECONDS` triggers `ReplApp._force_exit`. Because hang mode (1) leaves
the event loop alive, the key event is delivered and this handler runs, escaping without any
wait even though the worker thread is permanently stuck. (A single Ctrl+C keeps its existing
meaning: abort the in-flight turn, or — with nothing running — the ordinary quit flow.)

### 3. Liveness watchdog (hang mode 2)

`klorb.watchdog.LivenessWatchdog` is a daemon thread that the event loop "snoozes" on a Textual
`set_interval` timer (`ReplApp._snooze_watchdog`). The snooze is driven by the **event loop**, not by
the agent — a model that streams no tokens for minutes is irrelevant, because token streaming
happens on the worker thread while the main-thread loop keeps ticking its timers. The watchdog
fires only when the main thread stops servicing its own timers for `watchdog.timeout` seconds:
exactly hang mode (2), the one thing neither the key-event feedback nor the double-Ctrl+C can
escape. On firing it runs the same `_force_exit` path from its own thread, so it works even when
the main thread and event loop are completely wedged.

`watchdog.timeout` (see docs/specs/process-and-session-config.md) defaults to 10 seconds.
Setting it to `0` or a negative value disables the watchdog entirely.

### Shared force-exit path

Both the double-Ctrl+C handler and the watchdog call `klorb.watchdog.force_exit`, which:

1. runs a best-effort cleanup callback in a **separate daemon thread** — dumping every thread's
   stack (`klorb.diagnostics.dump_all_thread_stacks`) and, when the workspace is trusted, saving
   the session to `last-session.json` (`write_last_session`);
2. `join`s that thread for at most `_FORCE_EXIT_CLEANUP_GRACE_SECONDS`, so a cleanup that itself
   wedges (e.g. a save blocked on the very resource that caused the hang) can't prevent exit;
3. calls `os._exit(1)` unconditionally.

The all-thread stack dump is the point of this whole design's diagnostics: the next time klorb
hangs, the dump records exactly where every thread — especially the worker thread — is parked,
turning "why did it hang?" from guesswork into a captured stack trace.

## `_turn_in_flight` and why it is never cleared from outside the worker

`_turn_in_flight` is cleared only in `_finish_turn`. Every path where `Session.send_turn`
*returns or raises* reaches `_finish_turn` via one of the terminal handlers
(`_finalize_streamed_response`, `_show_response`, `_show_error`, `_handle_aborted_response`,
`_finish_shell_command`). To make that guarantee total, `_send_prompt` and `_run_shell_command`
call `_finish_turn` from a `finally` backstop (`_ensure_turn_finished`, a no-op if a handler
already finished the turn) so that even a `BaseException` — `asyncio.CancelledError` / worker
cancellation, which slip past `except Exception` — still clears the flag whenever the worker
unwinds at all.

What is deliberately **not** done is clearing `_turn_in_flight` from the event-loop thread while
the worker is still alive. Python cannot kill a thread; a worker blocked forever inside
`send_turn` (hang mode 1) cannot be terminated. If the flag were cleared and a new turn allowed
to start, the zombie worker would still be live, and if it ever woke (e.g. a bash command
finally hitting its own timeout) it would fire `call_from_thread(_finalize/_finish_turn)` and
corrupt the *new* turn's state — exactly the concurrent-worker corruption `_turn_in_flight`
exists to prevent. The only correct response to a worker that never unwinds is to exit the
process, which is what the double-Ctrl+C and watchdog paths do. See
docs/adrs/liveness-watchdog-over-reactive-arming.md.
