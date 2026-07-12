# Render the thinking body as Rich console markup, not a Markdown widget

* Superseded-by: [[style-arbitrary-text-spans-with-content-not-escaped-markup]] — the
  `rich.markup.escape()` + `[italic]` recipe below was later replaced by a `markup=False`
  `Static` styled via the `.thinking-body` CSS class, because `escape()` is less conservative
  than the parser and can't be trusted to neutralize arbitrary reasoning text. The Markdown vs.
  console-markup finding (below) still holds; only the escaping approach was abandoned.
* Date: 2026-07-01 00:00
* Question: The REPL's thinking block needs its body rendered in italics (see
  [[terminal-repl]]). The first implementation reused the same approach as the response
  body — a Textual `Markdown` widget, fed `f"*{accumulated_text}*"` so the `*...*` emphasis
  syntax would italicize it. Against a real thinking-capable model (`poolside/laguna-m.1:
  free`, see [[model-framework]]), the `<Thinking>` label rendered correctly but the body
  text came out unitalicized. Why, and what should render it instead?
* Answer: Switch the thinking body from a `Markdown` widget to a second `Static` widget
  (`.thinking-body` CSS class), with its content built by `klorb.tui.repl._italicized()`:
  `rich.markup.escape(text)` wrapped in `[italic]...[/italic]` Rich console markup, rather
  than Markdown's `*...*` emphasis syntax.
* Reasoning: Markdown inline emphasis (`*...*`/`_..._`) is a span-level construct — it
  cannot apply across blank-line-separated blocks, because a Markdown parser treats text
  separated by a blank line as separate paragraphs, each needing its own emphasis markers.
  Real reasoning traces routinely contain blank-line-separated paragraphs, so wrapping the
  *entire* accumulated thinking text in one pair of `*...*` and handing it to a `Markdown`
  widget produced literal, unrendered asterisks around unitalicized paragraphs instead of
  italic text — the bug the user observed. `Static` widgets, by contrast, accept Rich
  console markup directly (`markup=True` by default) and apply styling like `[italic]` to
  every line the renderable produces, regardless of blank lines in between, so the fix was
  to stop treating the thinking body as Markdown content at all. Escaping with
  `rich.markup.escape()` first is necessary because `Static`'s markup parsing would
  otherwise misinterpret literal `[`/`]` characters in the model's own reasoning text (e.g.
  `"check [status]"`) as the start of a markup tag.
