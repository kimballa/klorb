# `PermissionAskItem`/`PermissionAskContext` carry `command_text` as its own field

* Date: 2026-07-08 20:00
* Question: None of `BashTool`'s ask items — `CommandRules`-driven (`resource_description` =
  `f"run command: {' '.join(argv)}"`), redirect-driven (`f"{action} {path}"`), or structural
  (`BashCommandAnalysis.forced_ask_reasons`, e.g. "command has a non-literal argument
  (variable/command substitution/glob expansion)") — carried the model's original, full,
  unparsed command string anywhere. A structural item felt this worst: its `resource_description`
  is a *property* of the command ("has a non-literal argument"), not the command itself, so
  `PermissionAskScreen` had nothing to show the user but that abstract reason, with no indication
  of what command it was even about. Should the raw command text be folded into
  `resource_description` itself (a quick fix, tried first — see this ADR's git history), or does
  it deserve to be its own field?
* Answer: Its own field. `PermissionAskItem` and `PermissionAskContext` both gained
  `command_text: str | None` — set (to the same full command string) on every ask item a
  `BashTool` call produces, regardless of which of `path`/`command`/neither it also carries, and
  left `None` for every other tool's ask items (`resource_description` there already names the
  resource in full, e.g. `f"write to {path}"`). `resource_description` itself goes back to being
  exactly what `klorb.permissions.shell_parse` or `_classify` produces — the bare reason for a
  structural item, `f"run command: {argv}"` for a `CommandRules` item, `f"{action} {path}"` for a
  redirect — with no command text folded into it.
* Reasoning: Baking the command text into `resource_description` (`f"run command:
  {command}\n\n{reason}"`) was tried first and technically worked, but it conflated two things a
  UI needs to treat differently: *what command is this about* (constant across every item a
  single `BashTool` call produces) and *what specifically does this one item need a decision on*
  (varies per item — the whole point of `MultiPermissionAskRequired` asking about several things
  in series). Folding them into one string meant `PermissionAskScreen` would have had to
  re-parse/split `resource_description` by convention (a fixed prefix, a `"\n\n"` separator) to
  recover the two pieces separately for layout purposes (a bold command block, then a distinct
  detail line below it) — string-parsing structured data back out of a string built for display
  is exactly the kind of fragile round-trip a dedicated field avoids.

  It also complicated `PermissionOverride.reasons`, the "once" bypass's matching set for
  structural items (see `klorb.permissions.table`): keyed off `resource_description` directly, so
  folding the command in meant the override comparison had to reconstruct the same combined
  string a second time (in `_classify`'s override-check loop) rather than compare the bare,
  parser-produced reason it already had in hand. A separate `command_text` field needs no such
  reconstruction: the override check goes back to comparing the bare `reason` string, exactly as
  before this whole feature existed.

  This generalizes cleanly to every `BashTool` ask item, not just structural ones — a
  `CommandRules`-driven or redirect-driven item benefits from `PermissionAskScreen` showing the
  full command too (see docs/adrs/permission-ask-screen-shows-a-header-command-preview-and-detail.md),
  since a compound command (`foo && bar`) can raise several independent ask items that each need
  their own decision, and seeing only one simple command's own argv (or one redirect's target)
  without the surrounding command it came from is exactly the same "what is this even about" gap
  a structural item had, just less severe.
