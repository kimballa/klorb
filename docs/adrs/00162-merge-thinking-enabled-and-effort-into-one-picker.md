# Merge "thinking enabled" and "thinking effort" into one Off/Low/Medium/High picker

* Date: 2026-07-26 02:10
* Question: The VS Code plugin exposed thinking's `enabled` and `effort` as two separately
  settable properties: **Klorb: Set Thinking** (an Enable/Disable QuickPick, which only then
  asked a follow-up effort QuickPick when enabling) and **Klorb: Set Thinking Effort** (an
  effort QuickPick reachable directly, which left `enabled` unchanged per
  `_klorb/setSessionConfig`'s "field left unset is left unchanged" contract). Once the status
  row grew a dedicated thinking chip (alongside the model chip) whose click needed to open some
  single picker, this became actively confusing rather than just verbose: re-enabling thinking
  via **Klorb: Set Thinking** forced a *second*, easy-to-fumble effort choice even when the
  effort itself hadn't changed, and a report from actual use showed a user picking `Medium` in
  that follow-up prompt out of habit/misreading, silently overwriting an effort of `High` they'd
  set moments earlier through the other command — with no error, since `None`/unset really does
  mean "leave unchanged" on the wire, exactly as designed. Should the two stay separate (one
  command per field, matching the wire's own two independently-optional fields), or should they
  present as one control?
* Answer: Merge them into a single choice with four mutually exclusive states: `Off`, `Low`,
  `Medium`, `High`. **Klorb: Set Thinking** is now the only thinking-related command (**Klorb:
  Set Thinking Effort** is removed); its `pickThinkingState()` QuickPick marks whichever of the
  four is current and, on a pick, sends both fields together in the same `setSessionConfig`
  call — `{thinking: {enabled: false}}` for `Off`, `{thinking: {enabled: true, effort}}` for the
  other three — never `effort` alone. The VS Code status row's thinking chip (`onPickThinking`,
  posting `{type: 'pickThinking'}`) opens this same merged picker, mirroring how the model chip
  opens `klorb.selectModel`.
* Reasoning: from the user's perspective these were never two independent facts, only one —
  "how hard should the model think" — where `Off` is just the bottom of the same scale as
  `Low`/`Medium`/`High`, not an orthogonal toggle layered on top of it. Presenting them as two
  properties invited exactly the failure the bug report showed: a control whose two "questions"
  are actually coupled reads as safe to answer independently, but isn't, because leaving one
  unset silently keeps whatever it happened to be a moment before — correct wire semantics, but
  a footgun as *two separate user-facing prompts* for what is really one four-way choice. Wire
  compatibility with `_klorb/setSessionConfig`'s independently-optional `thinking.enabled`/
  `thinking.effort` fields is unaffected: the merged picker is a client-side UX choice about how
  many QuickPicks to show, not a protocol change, and it still only ever sends fields it means
  to change. The TUI's own `ThinkingCommandProvider` (`klorb/src/klorb/tui/commands/
  thinking_commands.py`) keeps its separate toggle/effort commands for now — merging those too
  is tracked in `TODO.md` rather than done here, since the TUI's command-palette `Provider`
  shape (dynamic per-state labels, a modal `OptionList` screen) doesn't share code with VS
  Code's `QuickPick`-based commands and deserves its own pass.
