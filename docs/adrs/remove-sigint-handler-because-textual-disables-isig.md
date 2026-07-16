# Remove the process-level SIGINT force-abort handler

* **Date:** 2026-07-16
* **Question:** klorb had a process-level `signal.signal(SIGINT, _sigint_handler)` in
  `klorb.cli.main()` intended as a last-resort escape — a first `Ctrl+C` forwarded to the
  default handler, a second within 1s calling `os._exit(1)`. It never actually worked from the
  keyboard while the TUI was running. Keep it, keep-and-narrow it, or remove it?

* **Answer:** Remove it entirely. The in-TUI last-ditch escape is now the double-`Ctrl+C` key
  handler plus the `LivenessWatchdog` (see
  docs/specs/interrupt-and-liveness-watchdog.md).

* **Reasoning:**
  * Textual's `LinuxDriver` puts the terminal into raw mode with the `ISIG` termios flag
    cleared (unless `TEXTUAL_ALLOW_SIGNALS` is set). With `ISIG` off, a keyboard `Ctrl+C`
    generates **no `SIGINT`** — the `0x03` byte is delivered to stdin and dispatched by Textual
    as a `ctrl+c` key event. So the handler could not fire from a keyboard `Ctrl+C` while the
    TUI owned the terminal, which is precisely the situation it was meant to rescue. It was dead
    code for its stated purpose.
  * It retained marginal value only for an external `kill -INT` and for `Ctrl+C` during the
    brief windows when Textual is not holding the terminal (startup/shutdown). Those are not
    worth a bespoke double-signal state machine, and its comment actively misdescribed what it
    covered ("even when the TUI layer isn't processing input" — the one case it cannot handle).
  * Keeping a mechanism that looks like a safety net but isn't is worse than not having it: it
    invites reliance on a path that never fires. The watchdog fires from its own thread
    regardless of terminal state, and the double-`Ctrl+C` key handler covers the live-loop case,
    so nothing of value is lost.
  * Rejected alternative: setting `TEXTUAL_ALLOW_SIGNALS=1` to keep `Ctrl+C` a real signal. That
    reintroduces signal delivery only on the main thread (useless if the main thread is wedged),
    forfeits the graceful single-`Ctrl+C` turn-abort key gesture, and lets `Ctrl+C` tear out
    mid-render. A dedicated watchdog thread is strictly more robust.
