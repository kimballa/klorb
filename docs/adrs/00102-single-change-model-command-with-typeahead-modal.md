# Replace per-model palette entries with one "Change model" command and a typeahead modal

* Date: 2026-07-12 10:00
* Question: [[use-textuals-command-palette-for-model-selection]] gave every registered model
  its own `"Select model: <name>"` entry in the command palette. With five built-in models
  today and a user-extensible `$KLORB_DATA_DIR/models` directory (see
  [[back-models-with-json-resource-files-not-python-classes]]) that count only grows, and nothing
  in the palette shows which model is currently active without reading the header. Should
  model selection keep growing the palette's default listing by one entry per model, or move
  to a different UI?
* Answer: `klorb.tui.model_commands.ModelCommandProvider` now offers a single command,
  `"Change model (<current model name>)"`, mirroring `ThemeCommandProvider`/
  `ThinkingCommandProvider`'s existing "one palette entry that names the current value, opens
  a modal" shape. Selecting it pushes `ModelSelectionScreen`, a `ModalScreen` with a text
  `Input` filter box above an `OptionList`: typing narrows the list via the same
  `textual.fuzzy.Matcher` the palette itself uses, arrow keys move the highlight, and Enter
  confirms. The active model is marked the same way `ThemeSelectionScreen` marks the active
  theme.
* Reasoning: A `"Select model: <name>"` entry per model was fine for three models but doesn't
  scale — a `Ctrl+P` press with an empty query now dumps every registered model into the
  default listing alongside every other system command, and a growing
  `$KLORB_DATA_DIR/models` directory would make that worse without bound. `ThemeSelectionScreen`
  already solved this exact problem for Textual's theme list (also unbounded, also needs
  browsing) with a dedicated modal instead of one system command per theme; giving models the
  same treatment reuses a pattern already proven in this codebase rather than inventing a new
  one. Textual's `OptionList` has no built-in text-filtering of its own, so the modal pairs it
  with an `Input` and re-filters the option list on every keystroke using the same fuzzy
  matcher `Provider.matcher()` wraps — the same ranking/highlighting behavior the palette
  itself uses, just scoped to model names instead of every command. The single palette entry
  still names the current model in its label (matching `ThemeCommandProvider`'s
  `"Change theme (<current>)"` convention) so the active model is visible without opening the
  modal at all.
