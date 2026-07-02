# EditFile covers insert and delete via its replace-range semantics, not dedicated tools

* Date: 2026-07-01 15:00
* Question: Alongside a line-range-replace tool (`EditFile`) and a search/replace tool
  (`ReplaceAll`), does the model also need dedicated "insert a line" and "delete a line
  range" tools?
* Answer: No. `EditFile` alone covers both: insert without deleting by setting
  `start_line == end_line` and folding that line's original text into `new_text` (e.g.
  prepend or append the new content around it); delete a range by passing an empty
  `new_text`. The one exception is an empty file (`total_lines == 0`), which has no anchor
  line to fold new content into — the only valid call there is
  `start_line=1, end_line=0, start_text="", end_text=""`, a deliberate zero-width range
  accepted only in that one case.
* Reasoning: A dedicated insert/delete tool would just be an alternate encoding of the same
  underlying splice operation `EditFile` already performs, at the cost of two more tool names
  for the model to choose between — more tool-selection surface, not less. The
  anchor-line-replace convention for insertion also avoids the general zero-width-range
  ("insert before line N" via `end_line = start_line - 1`) footgun: that convention is a
  classic off-by-one hazard even for human developers, and doubly so for a model without a
  debugger to catch the mistake. Restricting the zero-width range to the single empty-file
  case, where no anchor line can exist by definition, keeps that ambiguity from leaking into
  the common case.
