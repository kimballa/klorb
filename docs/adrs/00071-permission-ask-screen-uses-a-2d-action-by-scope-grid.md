# `PermissionAskScreen` is a 2D Allow/Deny × scope grid, not a flat list of six choices

* Date: 2026-07-07 23:54
* Question: `PermissionAskScreen` originally presented a flat `OptionList` of six choices — Allow
  (once/session/workspace/homedir), Deny, and Other — reflecting that only `Allow` had graduated
  scopes; `Deny` was a single fixed action. Once `apply_permission_grant()`/
  `apply_command_permission_grant()` gained a `Deny` direction with the same four scopes `Allow`
  already had (see
  [the grant-writer generalization ADR](generalize-grant-writer-for-deny-and-mirror-it-for-commandrules.md)),
  a flat list would have needed eight entries instead of five, plus Other — and the two axes
  (which action, what scope) are logically independent, not eight unrelated choices. How should
  the modal expose both axes without either overwhelming a flat list or hiding the symmetry
  between `Allow` and `Deny`?
* Answer: A 2-column (`Allow`, `Deny`) by 4-row (`once`, `session`, `workspace`, `homedir`) grid,
  navigated with arrow keys: Left/Right cycles the action column, Up/Down cycles the scope row,
  Enter confirms the highlighted cell. Escape remains a fast path straight to a once-scoped
  denial, without needing to navigate the grid first. `Other...` (now bound to the `O` key) stays
  a separate escape hatch outside the grid entirely — swapping it for a free-text `Input`, exactly
  as before — since free text isn't a point on either axis and forcing it into the grid as a
  ninth cell would break the 2×4 symmetry for no benefit.
* Reasoning: The grid's two axes are already how a user thinks about the decision — "do I want
  this at all" and "for how long" are independent questions, and rendering them as independent
  cursor axes makes that structure visible rather than requiring the user to scan eight
  similarly-worded flat entries ("Allow (once)" vs. "Deny (once)" vs. "Allow (this session)" ...)
  to find the one they want. Arrow-key navigation over a small fixed grid is also a more direct
  match for keyboard-only interaction than an `OptionList`, whose sequential up/down-only
  traversal doesn't reflect a two-axis structure at all. Keeping `Other` outside the grid avoids
  designing a "what does the ninth cell mean" answer for a case that isn't actually a scope/action
  combination — it's a distinct kind of response (redirect the model with an explanation) that
  happens to also deny the current call as a side effect, which is exactly how it behaved under
  the old flat-list design too.

  `ReplApp` additionally remembers the `action`/`scope` of the most recently answered prompt
  (`_last_permission_action`/`_last_permission_scope`) and seeds the next `PermissionAskScreen`'s
  cursor from it. This matters specifically because of
  [the serial multi-item ask ADR](ask-independent-items-serially-not-just-the-strictest.md): a
  single compound `BashTool` call can now raise several prompts in a row, and a user who just
  picked "Allow, this session" for the first item is very likely to want the same answer for the
  second — reseeding the cursor saves re-navigating to the identical cell every time, without
  taking away the ability to navigate elsewhere for an item that genuinely warrants a different
  answer.
