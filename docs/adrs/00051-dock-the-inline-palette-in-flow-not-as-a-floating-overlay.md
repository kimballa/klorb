# Mount the inline command palette in the normal layout flow, not as a floating overlay

* Date: 2026-07-04
* Question: [[command-palette-from-prompt]] calls for a popup shown "just above the prompt
  textbox" when the user types `>`. Should that be a true floating overlay (Textual `layer`s
  with absolute positioning) drawn on top of the conversation history, or a normal widget
  mounted directly above `PromptInput` in `ReplApp.compose()`'s existing top-to-bottom flow?
* Answer: Mount `PromptPalette` as an ordinary flow widget, immediately preceding
  `PromptInput` in `compose()`. Showing it pushes the history's `VerticalScroll` up by its
  height rather than drawing over it; hiding it (`display: none`) gives that space back.
* Reasoning: `PromptInput`'s own height is already dynamic (`TextArea` grows with wrapped
  content up to `ProcessConfig.prompt_input_max_lines`), so a true overlay positioned "just
  above" it would need to track that height on every keystroke to avoid drifting away from
  the input box it's meant to sit against. Textual's `layer`/absolute-offset positioning can
  do this, but it adds a second thing (input height, in addition to popup content) the popup
  has to stay synchronized with, for a purely cosmetic difference — the existing `Vertical`
  layout of `ReplApp.compose()` already reflows correctly with the input box's own growth,
  since siblings after a grown widget just move down; extending that same mechanism to the
  popup costs nothing and can't drift out of sync with the input's height by construction.
  The tradeoff is that showing/hiding the popup visibly shifts the conversation history above
  it, rather than transiently covering it — accepted as a reasonable difference for a v1,
  revisit only if it reads as jarring in practice.
