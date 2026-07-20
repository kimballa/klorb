# EditFileCore/CreateFileCore compute and persist the full diff, rather than re-diffing at render time

* Date: 2026-07-19 20:00
* Question: A TUI diff preview needs the actual line-level diff of an edit/create call, not just
  the `(+A/-R)` line-count delta `EditFileTool`/`CreateFileTool.summary()` compute today. Where
  should that diff be computed, and how should a UI (live or restored-session) get at it: recompute
  it at render time by re-reading the file's current content and the original request's `new_text`,
  or compute it once, at `apply()` time, and store it in the result dict alongside `post_edit_content`?
* Answer: Compute once, inside `EditFileCore.apply()`/`CreateFileCore.apply()`, using the `old_lines`
  already read off disk before the edit and the `new_lines` just written — via
  `klorb.tools.util.diff_lines.build_diff_hunks()` — and store the result as jsonable `DiffHunk`s
  under `result["diff"]`. A `Tool`'s `diff_preview()` override only ever parses that field back
  into `DiffHunk`s; it never diffs anything itself.
* Reasoning: `result` is what actually persists. `Session._run_tool_calls`'s
  `_format_tool_response_content()` already does `json.dumps(result)` for every successful call,
  and `RenderingMixin._render_restored_tool_call` `json.loads()`s it back to reconstruct a
  restored session's history — the same round-trip every other result field (`post_edit_content`,
  `new_total_lines`, ...) already goes through. Storing the diff there means a restored session
  renders an identical diff to the one shown live, with zero extra I/O and no dependency on the
  edited file still existing, still being readable, or having the same content it did at the time
  of the edit (a later turn, or a human, may well have changed it again by the time the session is
  reopened). Computing it at render time instead would require either keeping the pre-edit content
  around separately (nowhere obvious to put it that survives a process restart) or re-reading the
  file's current-on-disk state and hoping it still matches what the edit actually produced — silently
  wrong the moment it doesn't. Computing it once, where the before/after are both already in hand
  (`EditFileCore.apply()` reads `all_lines` before resolving the edit and produces `new_lines`
  right after), avoids both problems at essentially no extra cost: one `difflib.SequenceMatcher`
  pass alongside a substitution the core was doing anyway.
