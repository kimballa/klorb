# Ask about every independent item in a compound tool call serially, not just the strictest one

* Date: 2026-07-07 23:54
* Question: `BashTool` can find several independent things worth asking about in a single parsed
  command — more than one simple command needing a `CommandRules` decision, one or more
  redirection targets needing a `readDirs`/`writeDirs` decision, and structural forced-ask
  reasons the walker itself raised. The original design collapsed all of that down to a single
  `PermissionAskRequired` for whichever contributor produced the strictest verdict, via
  `klorb.permissions.table.stricter_verdict`. If a compound command like
  `make test && git push origin main` needs confirmation for both the `make test` pattern and the
  `git push` pattern, should the user see one merged prompt for "the sketchiest thing found," or
  a prompt per item?
* Answer: A prompt per item, asked in series. `BashTool._classify` now collects every
  independent `"ask"` contributor into its own `klorb.permissions.table.PermissionAskItem` and
  raises a single `MultiPermissionAskRequired` carrying all of them (still short-circuiting to an
  outright `PermissionError` if *any* contributor is `"deny"` — the strictest-wins rule still
  applies to that half of the decision). `Session._resolve_multi_permission_ask` walks
  `items` in order, calling `on_permission_ask` once per item — so the TUI shows a fresh
  `PermissionAskScreen` for each one — and stops at the first item answered `action="deny"` (or
  with `other_text` set): the remaining items are never asked about, and the whole call is
  denied. `_retry_after_multi_permission_decisions` then applies every collected persistent-scope
  grant and retries the call exactly once, not once per item (see
  [the `PermissionOverride` generalization ADR](generalize-permission-override-to-a-set-of-resources.md)
  for how a single retry can still honor several independent `"once"` decisions at once).
* Reasoning: Collapsing several unrelated concerns into one prompt forces the user to either
  approve or reject the whole bundle without seeing what's actually in it — a user who's fine
  running `make test` but wary of an unreviewed `git push` has no way to say so under a
  single-prompt design; they can only approve or block the entire compound command. Asking in
  series, one independent resource at a time, gives the user the same granularity of control over
  a compound shell command that they already have over a single tool call, and matches how a
  human reviewing the command by eye would actually reason about it — item by item, not as one
  opaque bundle. Stopping at the first denial rather than collecting every answer up front avoids
  making the user sit through prompts for items that no longer matter once the call is already
  going to be refused.

  A corollary this change also had to fix: a bare command-pattern ask item (a `CommandRules`
  `"ask"` for a simple command with no filesystem target — e.g. `make test`) used to fail closed
  unconditionally, because `Session._run_tool_calls`'s old single-item path treated any
  `PermissionAskRequired` with `path=None` as un-actionable. That made the permission-ask flow
  useless for the common case of a plain command needing confirmation with no associated
  directory — `make test` would always be denied outright even under `permission_framework=
  "ask"`, never actually reaching a prompt. `MultiPermissionAskRequired`'s per-item handling
  removes that restriction: an item with no `path` is asked about (or auto-approved under
  `"auto"`) exactly like any other item, persisted through
  `klorb.permissions.command_grant.apply_command_permission_grant` when it has a `command`, or
  not persisted at all (only `"once"` is meaningful) when it's a structural item with neither.
