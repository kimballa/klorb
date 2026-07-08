# `PermissionAskScreen` shows a styled header, a bold command/path preview, then detail

* Date: 2026-07-08 21:00
* Question: `PermissionAskScreen` originally rendered everything — "Permission requested:
  <resource_description>" plus any granted-directory/command-pattern note — as one `Static`
  widget with embedded `"\n"`s. That worked while `resource_description` was always a single
  short line, but broke down once ask items could be about an actual command: a bash command can
  run to hundreds of lines (a heredoc-embedded script, say), and cramming it into one
  undifferentiated block gave the user no visual separation between "what kind of access is this"
  and "what exactly is the command/path" and "why does this specific item need a decision."
  Should the modal keep rendering as one blob of text, or does it need real structure?
* Answer: Real structure, as separate widgets (not one `Static` with embedded newlines) stacked
  in `#permission-ask-body`:
  1. A header (`PERMISSION_ASK_HEADER_ID`), styled `color: $text-warning; text-style: bold;` —
     "Permission requested: Run command" if `ask_ctx.command_text` is set, else "Permission
     requested: Write file"/"Read file" from `ask_ctx.path`/`is_write`, else a generic "Confirm"
     fallback.
  2. The command text or path itself (`PERMISSION_ASK_COMMAND_ID`), styled bold to set it apart
     from surrounding text — *not* boxed in its own bordered/nested container, which was tried
     first and rejected (see "Reasoning"). A `command_text` longer than
     `_MAX_COMMAND_PREVIEW_LINES` (6) is truncated to that many lines plus a `[more...]`
     indicator (`_MoreIndicator`, `PERMISSION_ASK_MORE_ID`) — clickable, or reachable via the
     screen's own `+` binding, but deliberately *not* focusable (see "Reasoning" for why);
     either route pushes `ExpandedCommandScreen`, a full-screen scrollable view of the complete
     text.
  3. `ask_ctx.resource_description` (`PERMISSION_ASK_DETAIL_ID`) — the specific detail for this
     one ask item, which can differ from the command/path preview above it (e.g. which simple
     command or redirect target within a compound line this particular item is about).
  4. The existing granted-directory/command-pattern note (`PERMISSION_ASK_GRANTED_ID`), if any.
  5. The grid.

  A blank line separates each present section from the next, via a shared `.ask-section-end` CSS
  class (`margin: 0 0 1 0`) applied to whichever widget actually ends a section — the command
  Static itself if it fits inline, or `_MoreIndicator` instead if truncated — rather than a fixed
  widget always carrying that margin, since which one is last varies.
* Reasoning: The first attempt at the command preview wrapped it in its own nested `Vertical`
  (`width: auto; height: auto`) so the trailing blank-line margin had one obvious place to live
  regardless of whether a `[more...]` indicator was present. That reproduced the exact
  `width: auto`-on-a-container-of-`1fr`-children collapse this project already hit once, for
  `#permission-ask-grid` itself (fixed the same way that first time: an explicit `width: 65`
  instead of `auto`, once it was clear `Grid`'s own auto-sizing couldn't be trusted): a
  `Static`'s own default CSS sets only `height: auto` (Textual's `Static.DEFAULT_CSS`), leaving
  `width` at the framework default of `1fr` — a fraction of the
  *parent's* resolved width. Nest that inside a `Vertical` that itself needs an `auto` width
  computed *from its children*, and neither side has anything concrete to resolve against: both
  the wrapper and its children collapsed to a zero-size sliver, confirmed by measuring actual
  mounted widget `.region`/`.size` via a headless `Textual` `App.run_test()` harness (not
  assumed from reading the CSS) — this project had already been burned once this session by
  trusting `width: auto` without measuring it, so the fix here was verified the same way, not
  guessed at twice.

  The chosen fix isn't "give the wrapper an explicit width" (which would have worked, mirroring
  the grid's own earlier fix) but removing the wrapper entirely: the command preview and its
  `[more...]` indicator are now direct children of `#permission-ask-body`, the same level as the
  header/detail/grid, all of which already had stable width from the grid's own explicit
  `width: 65` — a plain `Static` child of an already-width-stable `Vertical` sizes correctly with
  no override needed, exactly like the header and detail lines already did. This also produces a
  visually flatter, wider-reading command block instead of a narrower boxed-in one, which was the
  explicit design preference driving this choice over the "just add an explicit width" fix: a
  command a user needs to actually read shouldn't be squeezed into its own sub-frame merely to
  solve a layout bug that had nothing to do with framing in the first place.

  `_MoreIndicator` was originally `can_focus=True` with its own `Binding("enter", "activate", ...)`
  — the intent being "Tab reaches it, Enter (while focused) expands, without stealing Enter from
  the grid otherwise." That broke the grid instead: Textual's `Screen.AUTO_FOCUS` auto-focuses
  the first focusable descendant on mount when nothing else claims focus first, so
  `_MoreIndicator` — the *only* focusable widget this screen ever mounts — silently had focus
  from the moment the screen appeared, for any ask with a truncated command, no click required.
  A focused widget's own bindings are checked before a key event bubbles to the screen, so every
  Enter press permanently resolved to "expand" instead of `PermissionAskScreen.action_confirm`,
  making the grid's Allow/Deny selection completely unconfirmable by keyboard whenever a command
  was long enough to truncate — confirmed by inspecting `app.focused` in a headless `App.
  run_test()` session, which showed `_MoreIndicator` focused immediately on mount, no interaction
  needed. The fix is to not make it focusable at all: `can_focus` stays `Static`'s own default
  (`False`), so it can never intercept a key event, and `+` remains the only keyboard path to
  expand a command (the click path is unaffected, since clicking doesn't require `can_focus` to
  invoke `on_click`).
