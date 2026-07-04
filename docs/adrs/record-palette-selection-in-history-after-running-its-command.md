# Record a palette selection in input history after running its command, not before

* Date: 2026-07-04
* Question: `PromptInput._execute_palette_hit` both appends `>`+canonical-name to the input
  history and runs the selected command. Which should happen first?
* Answer: Run the command first (via `_run_palette_command`, deferred with `call_later`),
  and only append to `self._history` once it returns (awaiting it first if it's async).
* Reasoning: [[command-palette-from-prompt]]'s worked example requires that selecting
  `>Clear session` leave `>Clear session` itself recallable via up-arrow immediately
  afterward. But `clear_session()` — the very command being run — resets the input history
  from under it via `PromptInput.clear_input_history()`, as part of starting a session whose
  input history matches its empty conversation history (see
  [[clear-command-starts-a-new-session-and-log-file]]). Appending the canonical entry before
  running the command would have it immediately wiped by that reset, leaving nothing to
  recall — exactly backwards from the spec. Appending after the command has already run means
  the reset (if any) happens first, and the entry recording the very selection that triggered
  it survives as the new history's first entry. Commands that don't touch `_history` (model
  selection, thinking toggles) are unaffected either way.
