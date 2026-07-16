# A Ctrl+C that interrupts running activity force-exits on the third press, not the second

* **Date:** 2026-07-16
* **Question:** When Ctrl+C interrupts a running activity (an in-flight turn, a synchronous Bash
  tool call inside it, or a `!`-prefixed shell command), should a *second* Ctrl+C within
  `_DOUBLE_INTERRUPT_WINDOW_SECONDS`, while that same activity is still in flight, immediately
  `_force_exit()` the whole klorb process — or should it only warn, requiring a third press to
  actually force-exit, the same as a copy press does for the idle-quit ladder?
* **Answer:** A second Ctrl+C in this situation only shows the "Press Ctrl+C again to quit."
  notice; it does not re-send the interrupt signal and does not force-exit. It takes a third
  press within the window to `_force_exit()`. An interrupt press is treated exactly like a copy
  press for streak-escalation purposes — see docs/specs/interrupt-and-liveness-watchdog.md's
  "Ctrl+C semantics" section for the resulting state machine.
* **Reasoning:**
  * The interrupt path now actually sends `SIGINT` to a running Bash tool call's child process
    (see docs/adrs/bash-cancellation-via-session-active-cancel-event.md), which typically dies
    within a fraction of a second — well before a user could type a second Ctrl+C — but the
    `SIGKILL` escalation grace period (`_TIMEOUT_GRACE_SECONDS`) can still leave `_turn_in_flight`
    `True` for a few seconds in the worst case. Immediately force-exiting the entire klorb process
    just because a second Ctrl+C landed inside that ordinary grace window — while the activity was
    already on its way out — is disproportionate: it throws away the running session over a race
    the user didn't need to escalate at all.
  * Re-sending the same interrupt signal on every subsequent press doesn't help a command that's
    already ignoring it (SIGINT-immune, or a non-Bash blocking call `Session` itself is stuck
    inside), so there's nothing productive for a second press to *do* to the running activity
    itself — the only meaningful thing left for it to do is move the user one step closer to the
    "are you sure you want to quit" ladder, exactly like any other Ctrl+C that already did
    something (a copy).
  * A genuinely wedged worker (hang mode 1 in docs/specs/interrupt-and-liveness-watchdog.md) still
    only costs one extra keystroke to escape: interrupt, warn, quit — all three deliverable within
    `_DOUBLE_INTERRUPT_WINDOW_SECONDS` of each other if the user just keeps pressing Ctrl+C, so the
    fast-escape property the original double-Ctrl+C mechanism existed for is preserved, just with
    one more confirming press instead of a single hair-trigger one.
