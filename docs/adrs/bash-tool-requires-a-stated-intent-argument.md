# BashTool takes a required `intent` argument, separate from `command`

* Date: 2026-07-12 16:30
* Question: A command string alone (`grep -n _wait_until test_tui_repl.py`) tells a user *what*
  runs but not *why* — the approval dialog and history scroll have no independent signal to judge
  a command against, and the LLM risk classifier (`klorb.permissions.risk_classifier`) has nothing
  to compare the command's actual effect to. Should `BashTool` gain a way for the model to state
  what it's trying to accomplish, and if so, is it a required argument or an optional one?
* Answer: Add a required `intent` string argument to `BashTool`, independent of `command`: a
  short, plain-English statement of what the command is trying to accomplish (e.g. `"List all
  _wait_until call sites in test_tui_repl.py"`). It is threaded everywhere `command_text` already
  flows — `PermissionAskItem`/`PermissionAskContext`, `PermissionAskPanel` (an "Intent: ..." line),
  the history-scroll record (`format_ask_context_body`), `BashTool.summary()` (leading the
  rendered line), and `klorb.permissions.risk_classifier.classify_command_risk()` (a
  `<StatedIntent>` element). The classifier's system prompt instructs it to treat a command that's
  deceptively different from its own stated intent as a risk signal in its own right — raising the
  score and naming the mismatch in the rationale — independent of how risky the command would
  otherwise look in isolation.
* Reasoning: Required, not optional, for two reasons. First, the risk classifier's intent-vs-
  command comparison is only useful if there's reliably something to compare against — an optional
  field would mean the comparison (and the extra scrutiny it buys) silently stops applying whenever
  the model omits it, which is exactly the case a user would most want it to catch. Second, an
  optional field would make the approval dialog and history scroll inconsistent from one call to
  the next — sometimes showing a "what for" line, sometimes not — which is worse UX than always
  showing one. The cost is small: every `BashTool` call already requires the model to reason about
  what it's running (that's how it decided to run it at all), so stating that reasoning in one
  sentence is a low-cost, high-signal addition, not a new burden. `intent` is purely descriptive —
  never parsed, matched against a permission rule, or otherwise treated as part of the command
  itself — so a vague or slightly-off intent degrades gracefully to "no extra scrutiny gained" for
  that one call, rather than breaking anything.
