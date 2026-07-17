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

## Ctrl+C semantics

Pressing Ctrl+C does one of three things, decided fresh on every press:

1. **Selected text.** If there's a live text selection — either a screen-level mouse-drag
   selection, or a selection inside the focused `PromptInput` — Ctrl+C copies it to the
   clipboard and does nothing else. This is handled entirely by Textual's own binding
   resolution, never by `ReplApp.action_interrupt`: `PromptInput.action_copy` and
   `SelectionSafeScreen.action_copy_text` both sit earlier in the per-key binding chain (focused
   widget → ... → Screen → App) and, when there's something to copy, copy it and return without
   raising `SkipAction` — the chain walk stops there and `action_interrupt` never runs at all.
   Only when nothing is selected do both raise `SkipAction`, exactly like Textual's own base
   implementations, letting the chain fall through to the App-level `ctrl+c` binding
   (`action_interrupt`). Either override also calls `ReplApp._note_ctrl_c_copy()` to record the
   press for the streak bookkeeping below, since `action_interrupt` itself never sees it.
2. **Something running.** `ReplApp._turn_in_flight` covers both an in-flight model turn
   (including a synchronous Bash tool call running inside it) and a `!`-prefixed direct shell
   command. Ctrl+C interrupts it — identically to Escape (`action_abort_response`) — via
   `ReplApp._interrupt_running_activity()`, which shows the "Interrupting…" notice
   (`_note_interrupt_requested`) and sets whichever cancel event applies
   (`_shell_cancel_event`, or the turn's `_cancel_event` via `_signal_turn_cancellation`). See
   "Bash tool cancellation reaches the running child" below for what a turn's cancel event now
   does that it didn't before this feature existed.
3. **Nothing running, nothing selected.** Shows a "Press Ctrl+C again to quit." notice
   (`_note_ctrl_c_quit_warning`) and does nothing else.

Escape is bound only to case 2 (`action_abort_response`, hidden from the footer via
`check_action` unless `_turn_in_flight`) — it has no copy-text or quit-warning meaning, since
Ctrl+C's other two cases are specifically about a keystroke that's overloaded in a real
terminal, not about giving Escape more jobs.

### Ctrl+C streak bookkeeping and the escalation ladder

`ReplApp._last_ctrl_c_at`/`_last_ctrl_c_kind` record the timestamp and outcome (`"copy"`,
`"interrupt"`, or `"bare"`) of the most recent Ctrl+C press, of any of the three kinds above.
`action_interrupt` (and the two copy-hook overrides) update this pair on every press, and the
same method reads it to decide what *this* press does, all scoped to
`_DOUBLE_INTERRUPT_WINDOW_SECONDS`:

* Case 2 (something running) only actually interrupts once per streak: if the immediately
  preceding press, within the window, was itself an `"interrupt"` or a `"bare"` press, this press
  does **not** re-signal the running activity even if it's still in flight — it falls through to
  the warn/quit ladder below instead, exactly like case 3. This is deliberate: re-sending the
  same signal on every subsequent press wouldn't help a command that's already ignoring it, and a
  user holding Ctrl+C shouldn't have every repeat keystroke re-interrupt whatever happens to
  start running next.
* Otherwise (nothing running, or a running activity this streak already interrupted once): shows
  the "Press Ctrl+C again to quit." notice (`_note_ctrl_c_quit_warning`) — unless the immediately
  preceding press was *also* one of these "nothing new to do" presses within the window
  (`previous_kind == "bare"`), in which case this press force-exits (`_force_exit()`) instead.

Net effect: a solitary bare Ctrl+C (nothing selected, nothing running) takes **two** presses to
force-exit — warn, then quit. A copy or an interrupt takes **three** — the copy/interrupt itself,
then a warning (even if the interrupted activity is still shutting down), then a quit — since a
copy or an interrupt only ever counts as the first step in the ladder, never as the first of two
"nothing to do" presses. See docs/adrs/idle-ctrl-c-force-exits-not-the-polite-quit-flow.md for why
the final quit bypasses the polite `_quit_after_maybe_saving()` flow entirely rather than opening
its save-prompt modal.

### Interruptible-tool cancellation reaches the running work

`Session._dispatch_turn`/`_run_tool_calls` only check `callbacks.cancel_event` between tool
calls/rounds, and `Tool.apply()` itself blocks the worker thread for the whole lifetime of
whatever it's running — so a long synchronous tool call inside an in-flight turn would
otherwise be unreachable by Ctrl+C/Escape until its own next tool-call/round boundary.
`Session.active_cancel_event` (set to
`callbacks.cancel_event` for the duration of `_dispatch_turn`, see docs/adrs/bash-cancellation-
via-session-active-cancel-event.md) exposes that same event to the tool actually running via
`self.context.session.active_cancel_event`. The `klorb.tools.interruptible_tool.InterruptibleTool`
base class packages access to it as `_active_cancel_event()`, and every tool whose work can run
long enough to want mid-flight interruption inherits from it:

* `BashTool._execute`/`PersistentShell._run_raw` poll it while waiting on the child process and
  send it `SIGINT` immediately once it fires — see docs/specs/bash-tool-and-command-permissions.md's
  "Cancellation" section for the full protocol and the tool-response shape a cancelled call reports.
* `GrepTool`/`FindFileTool` poll it between directories (and, for `Grep`, before reading each file)
  as they walk the tree, stopping promptly and returning whatever partial results they had with a
  `cancelled: true` flag rather than finishing a search over a huge tree the user already abandoned.

Because `on_tool_call_started` fires for *every* tool before its `apply()`, all of these also mount
the shared `<Tool use>` + `Running…` spinner while they work (see docs/specs/terminal-repl.md), so a
slow search shows activity rather than appearing frozen.

## The three hang-mode escape mechanisms

### 1. Interrupt feedback: "Interrupting…"

`action_abort_response` (Escape) and `_interrupt_running_activity` (Ctrl+C, case 2 above) mount
a one-line `Interrupting…` notice into the history the first time they're pressed during a turn
(`ReplApp._note_interrupt_requested`, reset per turn in `_finish_turn`). This runs on the event
loop, so in hang mode (1) the user gets immediate confirmation the keystroke was received and
the app isn't silently ignoring them — the difference between "it's working on stopping" and
"it's deadlocked".

### 2. Repeated-Ctrl+C force-quit (hang mode 1)

A Ctrl+C streak that starts by interrupting a running activity (see "Ctrl+C streak bookkeeping"
above) reaches `ReplApp._force_exit` on its *third* press within `_DOUBLE_INTERRUPT_WINDOW_
SECONDS` — the first interrupts, the second only warns (even though the stuck activity is still
in flight), the third quits. Because hang mode (1) leaves the event loop alive, every one of
those key events is delivered and `action_interrupt` runs each time, escaping without any wait
even though the worker thread is permanently stuck. Never force-exiting on just the *second*
press avoids nuking the whole process for an activity (a Bash command SIGINT'd a moment earlier,
say) that was always going to end on its own within its grace period anyway — see
docs/adrs/idle-ctrl-c-force-exits-not-the-polite-quit-flow.md.

### 3. Liveness watchdog (hang mode 2)

`klorb.watchdog.LivenessWatchdog` is a daemon thread that the event loop "snoozes" on a Textual
`set_interval` timer (`ReplApp._snooze_watchdog`). The snooze is driven by the **event loop**, not by
the agent — a model that streams no tokens for minutes is irrelevant, because token streaming
happens on the worker thread while the main-thread loop keeps ticking its timers. The watchdog
fires only when the main thread stops servicing its own timers for `watchdog.timeout` seconds:
exactly hang mode (2), the one thing neither the key-event feedback nor a repeated Ctrl+C can
escape. On firing it runs the same `_force_exit` path from its own thread, so it works even when
the main thread and event loop are completely wedged.

`watchdog.timeout` (see docs/specs/process-and-session-config.md) defaults to 10 seconds.
Setting it to `0` or a negative value disables the watchdog entirely.

### Shared force-exit path

Both the repeated-Ctrl+C handler and the watchdog call `klorb.watchdog.force_exit`, which:

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
process, which is what the repeated-Ctrl+C and watchdog paths do. See
docs/adrs/liveness-watchdog-over-reactive-arming.md.

## See also

* docs/specs/bash-tool-and-command-permissions.md — the "Cancellation" section this feature's
  Ctrl+C/Escape signal actually drives for a running Bash tool call.
* docs/specs/session-persistence.md — the "Saving" section covering `SaveOnQuitScreen`, the
  Ctrl+Q quit-with-save-prompt flow this feature deliberately keeps separate from Ctrl+C.
* docs/adrs/bash-cancellation-via-session-active-cancel-event.md
* docs/adrs/idle-ctrl-c-force-exits-not-the-polite-quit-flow.md
* docs/adrs/ctrl-c-interrupt-escalates-on-the-third-press-not-the-second.md
* docs/adrs/liveness-watchdog-over-reactive-arming.md
* docs/adrs/remove-sigint-handler-because-textual-disables-isig.md
