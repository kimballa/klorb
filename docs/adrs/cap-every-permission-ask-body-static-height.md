# Cap every `PermissionAskPanel` body `Static` to a bounded number of rows

* Date: 2026-07-12 00:00
* Question: `PermissionAskPanel` already truncates the command *preview* to
  `_MAX_COMMAND_PREVIEW_LINES` with a `[more...]`/`ExpandedCommandScreen` expand path (see
  docs/adrs/permission-ask-screen-shows-a-header-command-preview-and-detail.md), which stops a
  heredoc-embedded script from pushing the decision grid off the bottom of the screen. But the
  preview is not the only variable-height widget in the panel: the `"Intent:"` line, the risk
  rationale, the per-item `resource_description` detail, and the granted-scope line are each a
  `Static` with `width: 1fr` and no height bound at all. Their text is model- or resource-derived
  and can be a single very long line with no `\n` (e.g. a `resource_description` of `"run command:
  <a 2000-character one-liner>"`), which soft-wraps to dozens of rows once rendered. The command
  preview itself is only content-truncated on the `preview_wrap_width` path `ReplApp` drives; a
  caller that doesn't pass one leaves even the preview unbounded for a single long line. Any one
  of these can reproduce the same "grid pushed off the bottom of the screen" failure the preview
  truncation was meant to fix. Should each of these widgets get its own bespoke truncation, or is
  there one guard that bounds them all?
* Answer: One guard. `PermissionAskPanel._cap_body_static_heights`, run at mount, clips every
  variable-content body `Static` to a fixed number of terminal rows: the command preview to
  `_MAX_COMMAND_PREVIEW_LINES` (matching its own content-level truncation budget, so this is a
  pure backstop for it), and the intent / rationale / detail / granted lines to the smaller
  `_MAX_SECONDARY_TEXT_LINES`. The cap is applied by setting `styles.max_height` /
  `styles.overflow_y = "hidden"` on each present widget, so however long the text is, and however
  much a single unbroken line soft-wraps, the panel can never grow tall enough to push the grid
  off screen. The secondary lines are informational and have no `[more...]` expand path of their
  own, so they are simply clipped rather than truncated-with-an-indicator; the full command is
  always still reachable via the command preview's own `[more...]`/`ExpandedCommandScreen`.
* Reasoning: A blanket height cap is the belt-and-suspenders property actually wanted here — "no
  body widget can ever push the grid off screen" — rather than N independent content-truncation
  routines that each have to re-derive a wrap budget and each risk being forgotten when a new body
  line is added later. It also degrades safely for a body line the panel doesn't yet render: a
  future addition inherits the guarantee the moment its id is listed in the cap table, instead of
  silently reintroducing the overflow.

  The caps are applied programmatically at mount rather than as static `DEFAULT_CSS` so the row
  counts live once, in Python, right next to the `_command_preview` truncation logic they mirror,
  instead of duplicating `_MAX_COMMAND_PREVIEW_LINES`'s value as a magic number inside a CSS
  string where the two could drift out of sync (see CLAUDE.md's rule against duplicated
  constants). The compose-time widget construction is left untouched; only `on_mount` styling
  changes, so the compose-only unit tests that inspect `_pending_children` are unaffected.

  Clipping (rather than mouse-scrollable `overflow-y: auto`) was chosen for the secondary lines
  because the panel captures the arrow keys for grid navigation, so a nested scroll region would
  not be keyboard-reachable anyway, and the one piece of content a user might genuinely need in
  full — the command — already has a dedicated, keyboard-reachable expand path. The granted-scope
  line is the one place where clipping could in principle hide information (a grant spanning many
  directories), but that text is realistically one directory or one command pattern; a
  `_MAX_SECONDARY_TEXT_LINES`-row budget holds far more than any ordinary grant, and bounding the
  grid's visibility was judged the more important guarantee.

  Verified by measuring actual mounted-widget `.region.height` in a headless `App.run_test()`
  harness (not assumed from reading the CSS) — this panel had already been burned once by trusting
  `width: auto` without measuring it (see the ADR referenced above), so the height cap was
  confirmed the same way: with every body line maxed out, each capped `Static` sits at its cap and
  the grid stays fully within the screen.
