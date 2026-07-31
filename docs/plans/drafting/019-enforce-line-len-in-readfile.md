
# Plan 019: Enforcing maximum line length in ReadFile tools

ReadFileCore also needs a maxLineLength parameter, similar to Grep's.
Unlike Grep, this should line-wrap rather than truncate.

so e.g. it produces lines like:

```plain
1|this is the first line
2|this is the second line and it is very
2|long, as shown by the repeated line number
2|in the left gutter.
3|here is the third line...
```

The actual wrapping point is controlled by `tools.readFile.maxLineLength`. Like `grep`, we default
to a value of 500 characters.

The number of these wrapped lines counts toward the 200 line return-size limit. That is, in the
example above where there are three lines beginning with `2|`, it shows 6 lines for `maxLines` purposes.
The wrapped continuation lines do *not* count as distinct line numbers for offset purposes (so e.g.
a read at `start_line=3` actually starts on the third true line of the file, regardless of how many
times lines 1 or 2 were wrapped).

If the line is long enough that we hit the 200 line cutoff (or whatever the configured `maxLines`
cutoff is) mid-line, `truncated` will be set to `True` in the result dict, and `next_start_line`
should be the *same line number* as that last line, rather than `last_line + 1` like it is today.

When truncated is set to true on a line experiencing wrapping, insert a "bonus" return element in
the results dict:

```plain
"truncation_cause": "The response ended mid-line. Resume with start_line=<num> to re-read starting
from that line to read the whole line."
```

If the line is **insanely long** then actually it may itself span more than 200 wrapped lines at the
configured line wrap size. The tool should actually accept a `wrap_width` argument that overrides
the default wrap width. This should not be advertised in the tool `parameters()` block, it's
unlisted. But if it's specified and it's a number greater than 0 (or a string that can be parsed
with `int(...)`), we respect it.

In this case, we add the following bonus return element in the results dict:

```plain
"truncation_cause": "The response ended in the middle of the first requested line. Resume with
start_line=<num>, wrap_width=<2*current_wrap_width> to re-read starting from that line to read the
whole line, or use start_line=<num+1> to just advance to the next line."
```
