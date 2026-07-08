# Always show `[more...]` for a compound command's ask items, even a short one

* Date: 2026-07-08 20:35
* Question: `PermissionAskScreen`'s command preview shows `command_text` in full, with a
  `[more...]` indicator appearing only once the command runs past
  `_MAX_COMMAND_PREVIEW_LINES` (6) lines. `command_text` is always the *whole* raw command a
  `BashTool` call was given, even for a `MultiPermissionAskRequired` item that's only asking
  about one piece of it (e.g. `foo && bar`, asked about one simple command at a time) —
  `resource_description` names just that one piece ("run command: foo"). When the whole command
  is short, it already renders in full above that per-item detail line. Is that enough, or does
  a compound command need something more to make clear that `resource_description` is only part
  of the story?
* Answer: No — a short compound command still gets the `[more...]` indicator
  (`PERMISSION_ASK_MORE_ID`), even though the preview above it already shows the complete
  `command_text` with nothing truncated. `PermissionAskItem`/`PermissionAskContext` gained an
  `is_compound: bool` field (`BashTool._classify` sets it to
  `len(analysis.simple_commands) > 1`), and `PermissionAskScreen.compose()` shows the indicator
  whenever `truncated or ask_ctx.is_compound` — not only on the line-count truncation condition
  it already had.
* Reasoning: The two conditions that make `[more...]` useful aren't the same thing. Line-count
  truncation solves "the full command doesn't fit on screen." `is_compound` solves a different
  problem: even a command that *does* fit on screen can still be several independent decisions
  bundled under one raw command line, and `resource_description` — the text right below the
  preview, in the position a user's eye naturally lands on to understand "what am I approving"
  — only ever names one of them ("run command: foo"). A user approving that item has no
  structural cue, short of noticing the `&&` themselves in the preview text above, that `bar`
  is a second, independently-evaluated command in the same call that will also run. `[more...]`
  existing unconditionally for any compound command turns "the full command happens to already
  be visible" into "there is always an explicit, consistent affordance to go double-check the
  full command", regardless of how long it is — the same signal a user gets for a truncated
  command, extended to any command where a per-item ask's own description isn't the complete
  picture.

  The alternative — leaving `[more...]` gated on truncation alone, on the reasoning that "the
  text's already fully visible, so there's nothing more to reveal" — was rejected because
  `ExpandedCommandScreen` isn't only useful for text that doesn't fit; it's also a deliberate,
  separate screen the user affirmatively navigates to, which is a stronger signal ("I want to
  look at the whole command by itself") than reading it inline alongside the grid and a
  per-item detail line competing for attention. Compound commands are exactly the case where
  that stronger signal earns its keep, even when nothing was actually cut off.

  `is_compound` is computed once, in `BashTool._classify`, from `analysis.simple_commands`
  rather than re-derived by the UI from `command_text` itself — the walker already knows the
  parsed shape (`klorb.permissions.shell_parse.BashCommandAnalysis`); re-parsing the raw string
  a second time in `klorb.tui.permission_ask_screen` to guess at "does this look compound" would
  duplicate logic that already exists, and worse, could disagree with it (e.g. treating a
  `&&` inside a quoted string as a separator when the real parser correctly didn't). The same
  field is set identically on every item a single `BashTool` call produces, mirroring how
  `command_text` itself is already set the same way on every item from one call — see
  [the ask-item command-text ADR](permission-ask-item-carries-raw-command-text-as-its-own-field.md).
