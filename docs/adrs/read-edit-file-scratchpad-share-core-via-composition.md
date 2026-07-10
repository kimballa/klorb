# ReadFile/ReadScratchpad and EditFile/EditScratchpad share mechanics via a composed *Core class

* Date: 2026-07-10 04:00
* Question: `ReadScratchpadTool`/`EditScratchpadTool` need almost the same line-range read and
  drift-tolerant row-extent substitution mechanics as `ReadFileTool`/`EditFileTool` — the only
  real difference is how the target `Path` is obtained (a model-supplied `filename`, checked
  against `readDirs`/`writeDirs`, vs. the fixed, harness-managed `Session.scratchpad.path` with
  no permission check at all) and a couple of description strings. Reimplementing each mechanic
  a second time for the scratchpad tools would duplicate a substantial, already fiddly
  algorithm (especially `EditFile`'s drift-tolerant candidate search). How should the two pairs
  share that logic — inheritance (`EditScratchpadTool(EditFileTool)`), a shared free function
  each `apply()` calls directly, or something else?
* Answer: Composition. `klorb.tools.util.ReadFileCore` and `klorb.tools.util.EditFileCore` each
  hold the *entire* subject-agnostic mechanic — argument validation, the line-range read or
  drift-tolerant substitution algorithm, and building the result dict (minus `filename`, which
  only the file-tool variant adds) — behind one `apply()` method. `ReadFileTool`/
  `ReadScratchpadTool` each construct a `self.read_file_core: ReadFileCore`;
  `EditFileTool`/`EditScratchpadTool` each construct a `self.edit_file_core: EditFileCore`. Each
  tool's own `apply()` shrinks to: resolve/permission-check its own `path` (or skip that
  entirely for the scratchpad variant), delegate to `self.read_file_core.apply(path, args)` /
  `self.edit_file_core.apply(path, args, subject=..., reread_hint=...)`, and add `filename` to
  the result if it has one. Each core also exposes `parameter_properties()`, returning the
  `start_line`/`end_line`(/`start_text`/`end_text`/`new_text`/`context_before`/`context_after`)
  JSON-schema properties shared by both tools in a pair, so `parameters()` isn't duplicated
  either — just `filename`'s presence/absence and the `required` list differ per tool.
* Reasoning: Inheritance (`EditScratchpadTool(EditFileTool)`) was rejected because the two
  tools' `Tool.name()`/`description()`/`parameters()`/`apply()` contracts genuinely differ (no
  `filename` parameter, no permission check, different tool name reported to the model) in ways
  that would require overriding most of the base class's public methods anyway — at that point
  inheritance buys little over composition while adding a real risk: a `Tool` subclass
  unintentionally inheriting `EditFileTool`'s workspace-confinement/permission-check behavior
  (or a future maintainer assuming it does, since it's "an EditFileTool") when the scratchpad
  variant must never perform that check at all (see
  docs/adrs/scratchpad-tools-bypass-permission-tables.md). Composition makes each tool's actual
  contract exactly what its own `apply()`/`parameters()` methods say, with no inherited base
  behavior to audit.

  A shared free function (mirroring the codebase's own `klorb.permissions.workspace` functions,
  or the original `resolve_line_range_edit` this replaces) was considered too, but a class was
  chosen for `EditFileCore` specifically because the drift-search algorithm's private helpers
  (`_find_drift_candidates`, `_context_matches_candidate`, `_minimal_disambiguating_window`,
  `_describe_candidate_neighbors`) all close over `self._drift_search_radius` — as instance
  methods on `EditFileCore` they read that value directly, exactly as they did as private
  methods on `EditFileTool` before this change, rather than needing to thread
  `drift_search_radius` through every one of their signatures as originally done in the
  standalone-function version. `ReadFileCore` follows the same shape for consistency between
  the two cores, even though its own mechanic is simple enough that the class/function choice
  matters less.

  `klorb.tools.line_range_edit` (the standalone module this superseded, itself an earlier
  extraction from `EditFileTool` to let `EditScratchpadTool` reuse the algorithm without
  duplicating it — see the removed module's own history) is folded entirely into
  `EditFileCore` rather than kept as a separate module `EditFileCore` calls into: once
  `EditFileCore` became the only caller, keeping the algorithm in a separate module bought
  nothing but an extra file and import to follow.
