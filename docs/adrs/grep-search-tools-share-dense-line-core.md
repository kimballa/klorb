# Grep and the Search tools share one dense-line search core

* Date: 2026-07-13 01:19

## Question

`GrepTool`, `SearchScratchpadTool`, and `SearchMemoriesTool` each re-implemented the same
line-search mechanics (validate a `queries` array, escape-and-alternate it into one pattern,
find matching line indices, merge context windows) with three subtly different result shapes.
Grep and scratchpad both returned `blocks` — a list of `{filename?, start_line, end_line, lines}`
wrappers — while memories returned one flat entry per matching line. Grep's `lines` had already
been converted to a compact dense string (`"*42|text"`) for token efficiency, but scratchpad
still returned per-line `{line_number, line, matched}` dicts and the enclosing `start_line`/
`end_line` block wrapper survived everywhere. Should these three tools share one implementation,
and is the `blocks` wrapper still carrying its weight now that every line is self-describing?

## Answer

Extract the shared mechanic into `klorb.tools.util.search_core` (`validate_queries`,
`compile_queries`, `match_line_indices`, `format_match_line`, `context_lines_for_matches`) and
have all three tools delegate to it, mirroring how the file/scratchpad/memory read-edit-create
tools already share `*Core` helpers from the same package.

Drop the `blocks` / `start_line` / `end_line` wrapper. Each dense line already carries a `*`/
space match marker and its own 1-based line number, so:

* Grep returns `files: [{filename, lines}]` — one entry per matching file, `lines` a flat list
  of dense strings covering all of that file's merged context windows.
* `SearchScratchpad` returns a single flat `lines` list.
* `SearchMemories` returns `results: [{namespace, filename, lines}]` — grouped by file (it
  previously returned one entry per matching line), `lines` again the flat dense list, with
  `context_lines = 0` since memory search reports only the matching lines themselves.

Where two merged context windows within one file aren't contiguous, the jump in the embedded
line numbers is what marks the gap; no separator line is emitted between windows.

`detail_view` caps shifted with the shape: grep caps `files` to 20 (`files_omitted`), scratchpad
caps `lines` to 60 (`lines_omitted`).

## Reasoning

* The dense line format (`format_match_line`) makes `start_line`/`end_line` pure redundancy:
  they only ever equaled the first and last embedded line numbers, which the reader can already
  see. Removing the wrapper is a straight token win on every multi-window result, and the model
  loses no information — a gap still reads unambiguously as a discontinuity in the numbers.
* Splitting a dense line back apart stays unambiguous because a line number never contains a
  `|`: splitting on the first `|` always recovers the number and the (possibly `|`-containing)
  text. So flattening doesn't cost parseability.
* Three copies of the escape-and-alternate + window-merge logic had already drifted (grep dense
  strings vs. scratchpad dicts; grep's regex/case-insensitive support vs. the always-literal,
  always-case-insensitive searches). One core keeps their behavior and their exact validation
  error messages in lockstep, and is the same composition pattern the `*Core` file helpers use,
  so it needs no new architectural precedent.
* `search_core` takes no `ToolSetupContext` — every function operates on plain lists of lines its
  caller already read — so re-exporting it from `klorb.tools.util` is cycle-free, exactly like
  the existing `ReadFileCore`/`EditFileCore`/`CreateFileCore` re-exports.
* Grouping memories by file (rather than one result entry per line) matches grep's per-file
  shape and reads better when a single memory has several hits. `match_count` is kept meaningful
  by counting individual matching (`*`-prefixed) lines, plus one for each filename-only hit
  (whose sole listed line is unmatched), preserving the previous count semantics.

### Rejected alternatives

* **Keep an explicit gap separator (a `--` line, like `grep -C`'s own output) between merged
  windows.** Rejected: the line-number jump already signals the break, and an extra sentinel line
  is exactly the kind of redundant token this change removes.
* **Keep `blocks` but drop only `start_line`/`end_line`.** Rejected: a one-element-per-file list
  of `{filename, lines}` is simpler than a list of single-key `{lines}` blocks, and the block
  boundary carried no information the line numbers didn't.
