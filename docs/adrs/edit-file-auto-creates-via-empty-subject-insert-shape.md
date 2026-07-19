# EditFile/EditMemory auto-create a nonexistent file via the empty-subject insert shape

* Date: 2026-07-18 21:40
* Question: `EditFileCore.apply()` never created files — a nonexistent `path` made
  `path.read_text()` raise a bare `FileNotFoundError: [Errno 2] No such file or directory:
  '<path>'`, propagated to the model verbatim, with no hint that `CreateFile`/`CreateMemory`
  exists or is the right next step. Separately, an agent that already knows a file has no
  content yet (it just read an empty result, or knows it's never been written) has to make a
  `CreateFile`/`CreateMemory` call before it can use `EditFile`/`EditMemory` at all — even
  though `EditFileCore` already has a dedicated shape for "insert into a subject with nothing
  in it" (`start_line=1, end_line=0, start_text="", end_text=""`, see
  [the insert/delete ADR](edit-file-covers-insert-and-delete-via-replace-range.md)) that's
  semantically identical to "create this file with `new_text` as its content." Should a
  nonexistent file be recoverable directly through that shape, and should the failure case get a
  better message?
* Answer: Both, and they're two sides of the same change in `EditFileCore.apply()`:
  * `path.read_text()`'s `FileNotFoundError` is now caught. If the normalized args are the
    empty-subject insert shape (`start_line == 1 and end_line == 0`), a missing `path` is
    treated exactly like an existing-but-empty one — `raw = ""` — and the rest of `apply()`
    proceeds unchanged, including `_resolve_line_range_edit()`'s existing `total_lines == 0`
    branch (which still requires `start_text == "" and end_text == ""`, unchanged from before
    this ADR). At the end, before the final `path.write_text()`, missing parent directories are
    created (`path.parent.mkdir(parents=True, exist_ok=True)`), mirroring
    `CreateFileCore.apply()`'s own behavior exactly, and the result gains `created: true` —
    same key `CreateFileCore.apply()` already returns, so a caller checking for it doesn't need
    a second convention.
  * Any *other* shape against a missing `path` (a specific line range, `old_text`, the
    single-line shortcut, etc.) still fails — this mechanic only ever creates a *whole new* file
    via the one shape that has no anchor to verify against; it can't materialize a file at an
    arbitrary line range out of nothing. But the failure is now a purpose-written
    `FileNotFoundError` naming a new `create_hint` parameter (the tool to use instead, e.g.
    `"CreateFile"` or `"CreateMemory"`) and reminding the caller of the direct-create shape,
    rather than the bare OS errno text.
  * `apply()` gained a required `create_hint: str` parameter, following the precedent
    `CreateFileCore.apply()` already set with its own `edit_hint: str` (naming `"EditFile"` /
    `"EditMemory"` for its mirror-image "already exists" error) — same idea, opposite direction.
    `EditFileTool` passes `"CreateFile"`, `EditMemoryTool` passes `"CreateMemory"`,
    `EditScratchpadTool` passes a value noting the case is unreachable (the scratchpad file is
    harness-managed and always exists, so it never hits this path in practice).
  * `EditMemoryTool` needed a small matching change beyond just passing `create_hint`: it used
    to hard-`raise FileNotFoundError` itself, before ever reaching `EditFileCore.apply()`, if
    `not path.is_file()`. That pre-check is removed — `EditFileCore.apply()` is now the single
    source of truth for whether a missing file is recoverable — and the `original_content` read
    (used to restore the file if the edit would leave a blank first line, per the memory
    file-format rule) becomes `None` when the file didn't already exist. The blank-first-line
    recovery path then branches: restore `original_content` if it's not `None` (the existing
    behavior, unchanged), or `path.unlink(missing_ok=True)` if it is — undoing the auto-create
    rather than trying to restore content that never existed, so a memory that would violate the
    topic-first-line invariant never survives on disk even transiently.
* Reasoning: The insert-into-empty-subject shape already had every property needed to double as
  "create": it takes `new_text` as the entire desired content, it has no anchor content to
  verify (there's nothing to anchor against), and it was already special-cased in
  `_resolve_line_range_edit()`. Recognizing "doesn't exist" and "exists but is empty" as the
  same case for this one shape avoids inventing a second, parallel creation mechanism or forcing
  an extra round-trip (`CreateFile` then `EditFile`) for a case the tool can already express
  losslessly in one call. Restricting it to exactly that shape (not, say, any call against a
  missing file with a `new_text`) keeps the "never silently do something the caller didn't ask
  for" property intact: `start_line=1, end_line=0, start_text="", end_text=""` is already an
  unambiguous, deliberate "there is nothing here, insert this" assertion, so treating a missing
  file the same way as an empty one doesn't add any new interpretation — it only extends which
  starting states satisfy an already-precise request.
