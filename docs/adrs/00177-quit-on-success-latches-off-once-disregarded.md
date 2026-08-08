# `--quit-on-success` latches off for the rest of the session once disregarded

* Date: 2026-08-08
* Question: `--quit-on-success` exits the REPL process once a model turn finishes with a
  response and nothing was queued during it — but is disregarded for a turn that errors, is
  aborted (Escape/Ctrl+C), or has a message queued during it (`klorb.tui.mixins.
  prompt_submission.PromptSubmissionMixin._finish_turn`). Once one of those "disregard" cases
  happens, should `--quit-on-success` stay disarmed for the rest of the process's life, or apply
  again to whatever turn runs next?
* Answer: Latches off permanently. The first turn whose outcome isn't a clean, nothing-queued
  success sets `ReplApp._quit_on_success = False`; no later turn re-arms it, no matter how
  cleanly it finishes.
* Reasoning: `--quit-on-success` exists for `klorb -m "..." --interactive --quit-on-success` —
  run one automated task and get out of the way once it's done, but stay open the moment
  anything needs a human. An error, an abort, or a mid-turn interjection is exactly that signal:
  a human is now looking at (or steering) the session, and having the process vanish out from
  under them on some later turn — one they never asked to be watched for — would be a surprising
  and unwelcome surprise, not a convenience. Latching matches the terminal-session intuition
  that once a task stopped being hands-off, it stays a hands-on conversation for the rest of
  that session.
