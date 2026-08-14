Textual (8.2.8) drops alpha from `$text-*` design tokens in inline `Content` styles

When `$text-muted` (value `auto 60%`) or `$text-disabled` (`auto 38%`) is used as an inline
style string in `Content.assemble(...)` / `Style.parse(...)`, Textual resolves it to a
full-intensity `auto` color (alpha 1.0) instead of a muted one. The TUI skill finder's
namespace/description therefore rendered bright white.

Root cause: design variables are tokenized with the CSS *value* tokenizer
(`textual.css.tokenize.tokenize_values`), which classifies `60%` as a `scalar` token; but
`textual.markup.parse_style` only recognizes a `percent` token (produced by the *style*
tokenizer) for alpha, so the `scalar "60%"` is silently ignored.

Only `$text`/`$text-muted`/`$text-disabled` use the `auto NN%` form, so only they break. In
every non-ANSI (truecolor) theme, `$foreground-muted`/`$foreground-disabled` are generated as
`foreground.with_alpha(0.6/0.38).hex` (8-digit hex, alpha embedded) and resolve correctly. So in
inline `Content.assemble` styles use `$foreground-muted`/`$foreground-disabled` (see
`tui/widgets/skill_finder.py` and `tui/widgets/file_finder.py`). Caveats: a theme can override
those via its `variables` (monokai pins `foreground-muted=#797979`), and ANSI themes lack
`$foreground-disabled` entirely (and can't express alpha anyway). The same `$text-*` tokens in
CSS rules (`color: $text-muted;`) are fine, since that path (`ColorProperty.__set__`) handles the
`%` alpha correctly.
