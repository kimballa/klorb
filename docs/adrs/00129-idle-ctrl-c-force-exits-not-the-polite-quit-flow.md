# A repeated idle Ctrl+C force-exits directly; it never opens the save-session modal

* **Date:** 2026-07-16
* **Question:** Before this change, a single Ctrl+C with nothing selected and nothing running
  immediately called `_quit_after_maybe_saving()` — the same polite quit flow Ctrl+Q uses,
  including its "Save session state before quitting?" modal. Terminal convention (and user
  expectation) is closer to "Ctrl+C again to quit" with no intervening dialog. Should idle
  Ctrl+C keep opening that modal, gated behind a warning, or should repeated Ctrl+C bypass it
  entirely?
* **Answer:** An idle Ctrl+C shows a one-line "Press Ctrl+C again to quit." notice and does
  nothing else. A second idle Ctrl+C within `_DOUBLE_INTERRUPT_WINDOW_SECONDS` calls
  `_force_exit()` (`os._exit`, via a best-effort time-boxed cleanup) directly — it never pushes
  `SaveOnQuitScreen`. Ctrl+Q (and `:q`/`/quit`/`/exit`) remain the deliberate, courteous quit
  path: they still show the save-prompt modal (now with an added "Cancel" button — see
  `klorb.tui.confirm_screen.SaveOnQuitScreen`) and go through the graceful `_begin_exit()`
  worker-drain sequence.
* **Reasoning:**
  * Ctrl+C is the terminal's own "stop what's happening" gesture, not the "quit the program"
    gesture — that's what makes it safe to bind to interrupting a running turn/Bash tool call
    without also being disruptive when idle. A idle-Ctrl+C-opens-a-modal design conflates the
    two: it turns a fast, forceful keystroke into one that blocks on a dialog the user has to
    dismiss, exactly the kind of friction Ctrl+C is supposed to avoid.
  * `_force_exit()` already runs a best-effort, time-boxed session save
    (`_collect_hang_diagnostics`, via `write_last_session` when the workspace is trusted and has
    messages) before `os._exit`, so nothing is silently lost for the common case — it's just not
    interactively confirmed.
  * Keeping the modal on Ctrl+Q preserves an explicit, low-stakes way to quit deliberately with
    full control over save/discard/cancel, for a user who wants that ceremony rather than a
    rapid double-tap.
  * The double-tap escalation reuses the same streak state (`_last_ctrl_c_at`/
    `_last_ctrl_c_kind`) that also governs the "interrupt a running activity" case, so a Ctrl+C
    that copied text or interrupted something first counts as the *first* "nothing to do" press
    for a following idle Ctrl+C, not the second — see docs/specs/interrupt-and-liveness-
    watchdog.md's "Ctrl+C semantics" section for the full state machine.
