# A `PermissionAskItem`'s preview shows its own statement, not the whole compound command

* Date: 2026-07-08 21:25
* Question: `PermissionAskItem.command_text` is the full, unparsed raw command a `BashTool` call
  originated from, set identically on every item a `MultiPermissionAskRequired` raises for that
  one call (see docs/adrs/permission-ask-item-carries-raw-command-text-as-its-own-field.md).
  `PermissionAskScreen` showed this same full `command_text` as the bold preview for every item.
  For `echo $SHELL; echo $HOME` — two independent structural (non-literal-argument) ask items —
  both prompts showed the identical `"echo $SHELL; echo $HOME"` preview and the identical generic
  `resource_description` ("command has a non-literal argument..."), with nothing distinguishing
  which approval request was actually about which command. Should the preview keep showing the
  whole raw command for every item, or does each item need to show just its own piece?
* Answer: Each item's preview shows just its own statement. `klorb.permissions.shell_parse`'s
  `SimpleCommand`/`ForcedAskReason`/`RedirectTarget` each gained a `source_text` field — the
  exact original source text of the one AST node (a `CallExpr`/`DeclClause`, or the owning `Stmt`
  for a redirect/backgrounding/stdin-consumer reason) that item is actually about, sliced
  directly from the raw command string via AST byte offsets rather than reconstructed from
  parsed parts. `PermissionAskItem`/`PermissionAskContext` gained the matching
  `item_command_text: str | None`, and `PermissionAskScreen.compose()` now previews
  `ask_ctx.item_command_text or command_text` instead of `command_text` alone.
  `command_text` — the whole raw command — is still what `[more...]`/`ExpandedCommandScreen`
  shows; only the prominent per-item preview changed.
* Reasoning: `command_text` and `item_command_text` answer two different questions, and
  conflating them into one preview field made every item in a multi-item batch visually
  identical, defeating the entire purpose of asking about each one separately (see
  docs/adrs/ask-independent-items-serially-not-just-the-strictest.md). `resource_description`
  was supposed to carry the per-item specificity instead, but a structural item's
  `resource_description` is the walker's own abstract reason text ("command has a non-literal
  argument..."), not a rendering of the command itself — it was never going to disambiguate two
  items with the identical reason.

  The alternative of computing `item_command_text` by re-joining argv (`' '.join(argv)`, the
  same approach `resource_description`'s `f"run command: {' '.join(argv)}"` already uses for a
  `CommandRules` item) was rejected for two reasons. First, it doesn't work at all for a
  structural (`ForcedAskReason`) item — the whole reason this class of item exists is that its
  argv isn't fully literal, so there's no `argv` to join in the first place; that's exactly the
  item type this bug report was about. Second, even where an argv exists, joining it with spaces
  silently drops original quoting (`git commit -m "a message"` would render as
  `git commit -m a message`), which the raw-source-slice approach preserves for free — the same
  reasoning already applied to `_word_literal`'s "quoting alone is not a bypass signal"
  treatment.

  Extracting `source_text` from the AST's own `Pos`/`End` byte offsets (rather than tracking
  through a hand-maintained parallel data structure) means every walker function that already
  had a `Stmt`/`Cmd` node in hand could compute its own scoped slice on demand, via a single
  `_node_text(node, analysis)` helper reading `analysis.raw_command` (set once in
  `parse_command`, alongside the AST walk) — no separate parameter threading needed beyond
  passing the *scope* node itself (the owning `Stmt`) into the few call sites
  (`_walk_redir`/`_check_stdin_consumer`) whose forced-ask reason is about a redirect or an
  unsafe stdin consumer rather than the command node they're handed directly. A `CallExpr`/
  `DeclClause` node already carries its own `Pos`/`End` narrower than the enclosing statement, so
  those sites need no extra scope parameter at all.

  Scoping a redirect's `source_text` to the *owning statement* (`"cat file > out.txt"`), not
  just the bare target (`"out.txt"`) or the command alone (`"cat file"`), was a deliberate choice:
  a redirect target in isolation is meaningless without the command it's attached to, and the
  command in isolation omits the very thing the redirect item is asking about. The owning
  `Stmt`'s full text is the smallest self-contained unit that answers "what is this ask item
  actually about" on its own.

  `ExpandedCommandScreen` continues to show the *whole* `command_text`, not the current item's
  `item_command_text` — `[more...]`'s entire purpose (see docs/adrs/
  always-show-more-indicator-for-compound-command-ask-items.md) is to let the user see everything
  else a compound command also does, beyond the one piece its own preview and
  `resource_description` describe; showing only the current item's own text there would make
  `[more...]` a no-op.
