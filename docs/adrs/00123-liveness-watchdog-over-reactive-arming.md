# Liveness watchdog (event-loop heartbeat), not reactive arming; never clear _turn_in_flight from outside the worker

* **Date:** 2026-07-16
* **Question:** For the last-ditch force-exit backstop, should the watchdog be *armed reactively*
  (when the user presses Ctrl+C / requests quit during an in-flight turn, fire if the turn
  hasn't unwound in N seconds) or run as an always-on *liveness heartbeat* the event loop snoozes?
  And separately: since a wedged worker leaves `_turn_in_flight` stuck `True`, should we add a
  path that clears it from the event-loop thread so the app stays usable?

* **Answer:** Use an always-on liveness heartbeat snoozed by a main-thread `set_interval` timer;
  fire when the loop stops snoozing it for `watchdog.timeout` seconds. Do **not** ever clear
  `_turn_in_flight` from outside the worker; a truly stuck worker is resolved only by exiting the
  process.

* **Reasoning:**
  * There are two hang modes (see docs/specs/interrupt-and-liveness-watchdog.md): a stuck worker
    with a live event loop, and a wedged event loop. Reactive arming requires the Ctrl+C key
    event to be *processed* to arm the watchdog — which is impossible in the wedged-loop mode,
    the exact case that currently forces an external `kill`. A heartbeat snooze driven by the event
    loop stops precisely when the loop wedges, so it detects that mode with no dependency on
    input being processed.
  * The heartbeat measures **event-loop liveness, not agent liveness.** Token streaming runs on
    the worker thread; the main-thread loop keeps servicing its `set_interval` timer even while
    the model is silent for minutes. So a slow/quiet model never trips it — only a genuinely
    wedged main thread does.
  * The stuck-worker-with-live-loop mode is already covered by the double-Ctrl+C key handler
    (the loop is alive to process the key), so the heartbeat only needs to own the wedged-loop
    mode. Reactive arming would add complexity to cover a case the double-Ctrl+C already handles
    while missing the case only a heartbeat can.
  * Clearing `_turn_in_flight` from the event-loop thread while the worker is still alive is
    unsafe: Python cannot kill the worker thread, and a zombie worker that later wakes would fire
    `call_from_thread(_finalize/_finish_turn)` and corrupt whatever turn is current by then —
    reintroducing the concurrent-worker corruption the flag exists to prevent. The flag is a
    symptom; the disease is a worker that never returns from `send_turn`. The correct response is
    a process exit (double-Ctrl+C / watchdog), not a flag reset. We still make the flag's normal
    clearing total via a `finally` backstop in the worker (`_ensure_turn_finished`) so a
    `BaseException` unwind also clears it — but that only helps when the worker actually unwinds.
  * `watchdog.timeout <= 0` disables the watchdog, giving an escape hatch (and a clean way for
    tests to avoid a background thread that could `os._exit` the test process).
