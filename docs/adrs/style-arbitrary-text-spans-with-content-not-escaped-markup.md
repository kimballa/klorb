# Style arbitrary-text spans with a `Content` object, never `escape()`-d inline markup

* Date: 2026-07-12 00:00
* Question: Several TUI widgets display arbitrary text — a bash command's argv, a filesystem
  path, a model-generated risk rationale, a permission grant's command pattern — that can
  contain `[`. Textual `Static` widgets parse their string content as console markup by default
  (`markup=True`), so a literal `[` in that text is read as the start of a markup tag. Passing
  such text straight through crashes the compositor with `MarkupError` the moment the widget
  reflows (observed live: a bash command `python -c "print([m for m in modes if
  m=='selection'])"` shown in `PermissionAskPanel`'s command preview took down the whole app).
  The obvious guard is `textual.markup.escape()`. Is escaping the fix, and what should render
  text that needs *part* of a line styled (e.g. the accented grant pattern set off from its
  surrounding prose)?
* Answer: Do not rely on `escape()`. For a widget whose *entire* content is the arbitrary text,
  construct the `Static` with `markup=False` and style the whole widget with CSS (`text-style:
  bold`, etc.). For a line that mixes fixed prose with an arbitrary, individually-styled span,
  build a `textual.content.Content` with the arbitrary text as an explicitly-styled span
  (`Content.assemble("prose ", (arbitrary, "$text-accent bold"))`) and hand that `Content` to the
  `Static` — a `Content` is already-resolved styled text, so its raw characters are never parsed
  as markup at all. `escape()` is not an option in either case.
* Reasoning: `textual.markup.escape()` is *less conservative than the parser that actually
  applies the markup* — it does not neutralize every construct the parser treats as special.
  Concretely, it leaves `$`-variable syntax alone: after escaping, a pattern like `echo [$HOME]`
  is still consumed by the parser and renders as `echo ` — the `[$HOME]` is silently dropped, not
  shown as literal text. Silent truncation is especially dangerous on the permission-grant line,
  whose entire job is to state exactly what a persistent Allow will grant; a user could approve a
  broader scope than the one displayed. `markup=False` sidesteps parsing entirely for
  whole-widget cases, and a pre-built `Content` sidesteps it for span-styled cases while still
  preserving the visual emphasis, so neither path depends on escaping being complete. This
  supersedes [[render-thinking-body-as-rich-markup-not-markdown]], whose original
  `rich.markup.escape()` + `[italic]` recipe was replaced by a `markup=False` `Static` styled via
  the `.thinking-body` CSS class for the same reason; that switch shipped in code without its own
  ADR, so this record captures the general rule the whole TUI now follows.
