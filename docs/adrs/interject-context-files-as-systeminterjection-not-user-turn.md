# Fold workspace context files into a one-shot SystemInterjection, not a separate pseudo-user turn

## 2026-07-09

## Question

`docs/adrs/inject-workspace-context-files-as-a-user-turn.md` had `Session` insert a standalone
`role="user"` `Message` — carrying the workspace's context-instruction files — at the front of
history, ahead of the first real user turn, tracked for idempotency via a
`self._context_files_seeded` flag (since a `role="user"` message can't otherwise be told apart
from a real user turn). Separately, `Session` already had a `<SystemInterjection>` mechanism
(`_wrap_system_interjection()`, `_pending_permission_framework_interjection`,
`register_standing_interjection()`) for prepending harness notices directly into a real turn's
own prompt/`Message`, tagged so the model can tell them apart from the user's own words. Should
the context-files feature keep its own separate pseudo-user-turn mechanism, or be folded into
the existing `SystemInterjection` mechanism as another one-shot case?

## Answer

Fold it in. `Session._build_context_files_interjection()` returns a body string (or `None`);
`send_turn()` wraps a non-`None` result in a `<SystemInterjection subject="ProjectGuidance">`
tag and prepends it onto the *first* turn's `prompt`, exactly like the existing
`PermissionFramework` one-shot interjection, before that turn's single `role="user"` `Message`
is constructed. There is no longer a separate message inserted into history for this.

## Reasoning

Two mechanisms doing the same conceptual job (telling the model "here is out-of-band harness
context, not something the user typed") is duplication for a future reader to reconcile.
`SystemInterjection` already solved idempotency (a one-shot field cleared after firing, or a
standing provider re-polled every turn) and already solved "make the harness notice
distinguishable from real user words" (the tag itself) — the context-files feature had
independently reinvented a weaker version of the same thing: a whole extra `Message` in
history, distinguished from a real user turn only by a side-channel boolean
(`_context_files_seeded`) that every piece of message-history-walking code had to know to skip
over.

Folding it in means:

* One conversation-history shape: exactly one `role="user"` `Message` per real turn, always.
  Nothing has to special-case "the second message might actually be harness-injected, check
  `_context_files_seeded`" — that flag now only gates *whether a string gets prepended*, not
  *how many messages exist*.
* The `<ContextFile filename="..." priority="N">` sub-tags (see
  docs/specs/workspace-context-files.md) give the model a more explicit, structured signal than
  the old `### filename` Markdown headers did, and compose naturally with the outer
  `<SystemInterjection subject="ProjectGuidance">` wrapper the same way `PermissionFramework`'s
  body does.
* This is still a one-shot, edge-triggered case, not a standing one (docs/adrs/
  standing-interjections-complement-one-shot-for-level-triggered-state.md already draws that
  distinction for a different reason — BashTool's live shell): the files are read once, at the
  first `send_turn()` call, and never re-read or re-prepended even if they change on disk or the
  workspace's trust status changes mid-session (see docs/specs/workspace-context-files.md's "Out
  of scope"). That matches `_pending_permission_framework_interjection`'s shape more closely
  than `register_standing_interjection()`'s, so it's implemented as its own one-shot check in
  `send_turn()` rather than a registered standing provider.
* It's prepended *after* (so it ends up outermost, i.e. first-read) the
  `PermissionFramework`/standing interjections already handled in `send_turn()` — project
  context is meant to read as the very first thing in the prompt, ahead of any other harness
  notice a given turn happens to carry.

`docs/adrs/inject-workspace-context-files-as-a-user-turn.md` is left as-is, recording the
decision that was correct for the design at the time (there was no `SystemInterjection`
mechanism yet to fold into); this ADR supersedes it for how the feature works today.
