# Estimate `PermissionAskPanel`'s command-preview wrap width from terminal size, not post-layout measurement

* Date: 2026-07-11 19:30
* Question: `PermissionAskPanel`'s command preview was truncated by counting explicit `\n`-
  delimited lines against `_MAX_COMMAND_PREVIEW_LINES` (`_command_preview`). This missed a
  single very long line with no `\n` at all: `len(lines) == 1` never triggers truncation, yet
  the `Static` it's rendered into soft-wraps that one logical line across many visual rows once
  actually displayed, which could blow well past the intended budget and push the Allow/Deny
  grid below it off the bottom of the screen. Fixing this means knowing how wide the rendered
  preview will actually be — but `PermissionAskPanel.compose()` (which builds the preview
  `Static`) can run detached from any app at all (every existing unit test constructs the panel
  directly and calls `.compose()` without mounting it), so nothing in `compose()` can reliably
  read the widget's own post-layout `.size`. How should the truncation logic get a wrap width to
  measure against?
* Answer: `ReplApp._confirm_permission_ask` — the only real caller that ever mounts this panel
  into an actual, sized terminal — computes an estimate from `self.size.width` (the app's own
  terminal width, always known once the app is running, with no layout-timing dependency) minus
  a small constant for the panel's own padding, floored at a minimum, and passes it in as the
  `preview_wrap_width` constructor argument. `_command_preview` uses `textwrap.wrap` against
  that width to count how many visual rows each logical line would soft-wrap to, truncating
  once the running total would exceed `_MAX_COMMAND_PREVIEW_LINES`. `preview_wrap_width`
  defaults to `None`, in which case `_command_preview` falls back to the original,
  newline-only counting — exactly matching every existing unit test's expectations, since none
  of them ever mount the panel into a real terminal.
* Reasoning: A terminal-width estimate computed once, before construction, keeps the truncation
  decision synchronous and available at `compose()` time — no `on_mount`/`call_after_refresh`
  dance to re-truncate after an initial over-wide render, and no risk of the panel needing a
  second pass that visibly reflows once mounted. It's necessarily an estimate (actual rendered
  width could differ slightly from `app.size.width` minus a guessed padding constant), but this
  is a soft budget for a preview, not a hard layout constraint — erring by a line or two in
  either direction is harmless, while never truncating at all (the bug being fixed) is not. The
  `None`-default fallback is what keeps this change purely additive: every caller that
  constructs a `PermissionAskPanel` without ever mounting it into a running app (i.e. every
  existing test) keeps the exact truncation behavior it already asserts on, so fixing the real
  bug required no changes to those tests at all.
