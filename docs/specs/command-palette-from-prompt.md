# Command palette from the prompt

## Summary

Typing `>` as the first character of the prompt textbox turns it into a typeahead search
over the same command palette Textual's own `Ctrl+P` modal offers — model selection, session
clearing, thinking on/off/effort, and Textual's built-in system commands (theme, quit, etc.)
— without leaving the prompt box or opening a separate search-box UI. The text after the `>`
is the search query; up/down arrows move a highlighted selection in a small popup shown just
above the input; enter runs the highlighted command. `Ctrl+P` still opens Textual's own modal
palette as an alternative entry point. See [[terminal-repl]] for the REPL this is part of and
[[use-textuals-command-palette-for-model-selection]] for why `Provider`/`Hit`/`DiscoveryHit`
are the underlying palette primitives.

## How it works

* `klorb.tui.palette` (`klorb/src/klorb/tui/palette.py`) defines the pieces shared between
  this inline typeahead and any future palette-driven UI:
  * `gather_palette_hits(app, query)` collects every `Hit`/`DiscoveryHit` every provider in
    `app.COMMANDS`/`app.screen.COMMANDS` yields for `query` (an empty `query` means
    `discover()`, matching `Provider._search`'s own contract), sorted by descending match
    score with an alphabetical (case-insensitive) tiebreak on each hit's `text` (`_sort_key`).
    A `DiscoveryHit` (what an empty `query` yields) always scores `0.0`, so the bare `>` full
    listing — where every row ties on score — comes out purely alphabetical, while a narrowing
    query still ranks its closest textual matches first, ties among *those* broken
    alphabetically too. It constructs and tears down each `Provider` fresh per call
    (`_post_init()`/`_shutdown()`), which is what actually makes `_search()` yield anything —
    `Provider._init_success` starts `False` and only flips once `_post_init()`'s scheduled
    `startup()` task completes.
  * `PromptPalette`, an `OptionList` subclass (`can_focus=False`, like Textual's own internal
    `CommandList`), renders the current hits as rows via `PaletteOption` (an `Option` carrying
    the `Hit`/`DiscoveryHit` it was built from). `show_hits()`/`hide()` swap its rows and
    visibility; `move_highlight(direction)` drives its cursor without it ever taking focus,
    and `current_hit` reads back the `Hit`/`DiscoveryHit` for whichever row is highlighted.
* `ReplApp.compose()` mounts a `PromptPalette` (id `prompt-palette`) directly above the
  `PromptInput`, in the normal layout flow rather than a floating overlay — see
  [[dock-the-inline-palette-in-flow-not-as-a-floating-overlay]] for why. It starts hidden
  (`display: none` in `PromptPalette.DEFAULT_CSS`) and only becomes visible while palette
  mode is active with at least one matching hit.
* `PromptInput._palette_mode` (a property) is `True` exactly when the current text starts
  with `>`, hasn't been dismissed for this draft (`_palette_dismissed`), and didn't just
  arrive via history recall (`_suppress_palette_during_recall` — see "History browsing"
  below). Whenever it's `True`, `_on_key` reroutes up/down/enter/escape to the popup instead
  of history recall/submission:
  * Up/down call `PromptPalette.move_highlight(-1 or 1)`.
  * Enter reads `PromptPalette.current_hit`; if it's `None` (the query ruled out every
    option), it falls through to `_record_and_submit()` — the same path an ordinary prompt
    takes — so `>asd3434j2asdadkjfkjl34kj` just gets sent to the model like any other text.
    Otherwise it calls `_execute_palette_hit(hit)`.
  * Escape calls `_dismiss_palette()`, which sets `_palette_dismissed` and hides the popup
    without touching the text, so the rest of the draft types as plain text.
* Every other key (typing, backspace, paste, `NEWLINE_KEYS`) falls through to `TextArea`'s
  normal handling as before, followed by `PromptInput._refresh_palette(key)`: it recomputes
  whether the text still starts with `>`, and if so and not dismissed, calls
  `gather_palette_hits()` for the text after the `>` and shows or hides the popup depending on
  whether anything matched. If nothing matched *and* `key` was one of `"space"`, `"tab"`, or
  a `NEWLINE_KEYS` entry, `_palette_dismissed` is set — so typing a query that rules out every
  command and then hitting space/tab/a literal newline "gives up" on the search and continues
  as an ordinary multi-word prompt, rather than re-querying every further keystroke. Any text
  not starting with `>` (most commonly an empty box) always clears `_palette_dismissed` and
  hides the popup, so a fresh `>` later starts palette mode again from scratch.
* `PromptInput._execute_palette_hit(hit)` clears the box, hides the popup, and defers running
  `hit.command` via `self.app.call_later(...)` (mirroring Textual's own
  `CommandPalette._select_or_command`, so a command that itself pushes a modal — e.g. `Set
  thinking effort`'s `ThinkingEffortScreen` — doesn't do so mid-keystroke).
  `_run_palette_command` is what actually runs there: it calls `command()`, awaits the result
  if it's awaitable, and only *afterward* appends `>` + the hit's canonical `text` to the
  input history. See
  [[record-palette-selection-in-history-after-running-its-command]] for why that ordering
  (not simply appending before running the command) matters.

### Standard vs. displayed names

A palette command's `Hit`/`DiscoveryHit` carries two independent strings: `match_display`/
`display` (what's rendered in the popup row, and what the fuzzy matcher searches against) and
`text` (what gets recorded as `>text` in the input history on selection). For most commands
they're the same string, since `Hit.__post_init__`/`DiscoveryHit.__post_init__` default `text`
to `str(display)` when a provider doesn't set it explicitly. Two provider behaviors matter
here:

* A command whose displayed label already varies with live state but should still always
  recall the same way — `ThinkingCommandProvider`'s `Set thinking effort (High)` — sets `text`
  explicitly to the undecorated root (`"Set thinking effort"`), since the parenthetical
  current-value suffix wouldn't itself match this same command again on a later recall.
* A toggle pair that displays exactly what was invoked — `Enable thinking`/`Disable
  thinking`, or `SessionCommandProvider`'s `Clear session` — leaves `text` at its default (the
  display string itself), so recalling it later shows exactly what was selected.

### History browsing

Recalling a past palette selection (e.g. up-arrow landing on `>Clear session`) shows it as
plain text rather than resurfacing the popup: `PromptInput._recall_history` sets
`_suppress_palette_during_recall` whenever it loads an entry from `self._history`, and
`_palette_mode` refuses to activate while that flag is set even though the recalled text
starts with `>`. Restoring the stashed in-progress draft (down-arrow past the most recent
entry) clears the flag again, since that's the user's own text, not a recalled entry.
`_detach_from_history()` (called on any text-mutating key) also clears it, so editing a
recalled `>`-prefixed entry turns it back into a live palette query.

### The `> palette` hint

`ReplApp` mounts a `Static` (id `palette-hint`) in the status row, to the left of the
`Footer`. `_update_palette_hint()` (called on mount and on every `TextArea.Changed`, i.e.
`on_text_area_changed`) shows `PALETTE_HINT_TEXT` (`"> palette"`) while the prompt input's
text is empty or exactly `>`, and hides it (empty string) otherwise. This replaces the
`Ctrl+P`-shows-in-the-footer hint Textual would otherwise offer for the `command_palette`
binding — that binding is registered with `show=False` (`App.__init__`, since it's not among
`ReplApp.BINDINGS`), so it was never actually shown in the footer to begin with; this hint is
what points the user at palette-from-prompt as the primary way in.

### Converting `/clear`

`SessionCommandProvider` (`klorb/src/klorb/tui/session_commands.py`) is the only provider
`>clear` needs to match: its `search()`/`discover()` yield a `Hit`/`DiscoveryHit` for
`CLEAR_SESSION_LABEL` (`"Clear session"`) alone. There's no special-cased `prompt_text ==
"/clear"` check anywhere in `ReplApp.on_prompt_input_submitted` — a bare `/clear` typed as a
prompt is sent to the model like any other text now, since the palette (`>clear`) is the only
way to reach `clear_session()`.

## Usage

```
> palette                       # shown in the status row while the box is empty
>                                # shows every command (discover())
>clear                          # narrows to "Clear session"
>cle<enter>                     # selects and runs "Clear session"; box shows ">Clear session"
                                 # afterward, and it's what up-arrow recalls next
>zzznomatch <more text><enter>   # no command matches; space dismisses the popup and the whole
                                 # line is sent to the model as an ordinary prompt
```

## Out of scope

* The popup is a normal in-flow widget mounted directly above the prompt input, not a
  floating overlay — see
  [[dock-the-inline-palette-in-flow-not-as-a-floating-overlay]]. Showing it pushes the
  conversation history up by its height rather than drawing on top of it.
* `gather_palette_hits()` re-constructs and re-tears-down every provider on every keystroke
  that reaches `_refresh_palette`. Cheap today since no `klorb` provider overrides
  `startup()`/`shutdown()` with real work, but a provider that did would pay that cost on
  every keystroke rather than once per popup session.
