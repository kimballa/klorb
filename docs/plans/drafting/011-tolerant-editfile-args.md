# Tolerant EditFile arguments

> **Draft.** This document describes a design for review, not a built feature. Nothing under
> `docs/plans/drafting/` should be treated as implemented, and nothing else should depend on it,
> until it's promoted to `ready/` and then archived.

## Summary

`EditFileCore` (the drift-tolerant row-extent substitution shared by `EditFileTool`,
`EditScratchpadTool`, and `EditMemoryTool` — see
[[read-edit-file-scratchpad-share-core-via-composition]]) currently demands a rigid five-field
call: `start_line`, `end_line`, `start_text`, `end_text`, and `new_text`, all required. Agents in
practice reach for edits that don't fit that shape cleanly — a single-line change where naming an
`end_line`/`end_text` equal to the start is pure ceremony, or a small multi-line block they'd
rather hand over verbatim as one string than split into a start anchor, an end anchor, and a
line-number span. Every time the call doesn't fit, the model burns a rejected tool call (and
often a recovery `ReadFile`) learning the exact ritual.

This plan widens the *accepted* argument surface of `EditFileCore.apply()` without widening its
*semantics*: every new form desugars into the same "locate an inclusive `[start_line, end_line]`
row extent near the hint, tolerating bounded drift, then substitute `new_text`" mechanic the
[[edit-file-tolerates-bounded-line-drift-via-local-candidate-search]] ADR already established.
Two convenience shapes are added:

* **A single-line shortcut.** When `start_line == end_line`, `end_text` may be omitted or empty;
  `start_text` alone anchors the one line being replaced.
* **An `old_text` block form.** Instead of `start_text`+`end_text`, the caller may supply
  `old_text`: the *entire* contiguous block to replace, verbatim, as one multi-line string. It's
  fuzzy-anchored to the line hint with the same drift window; `end_line` becomes optional
  (inferred by counting `old_text`'s lines) and, when supplied, must agree with that count. In
  `old_text` mode the line hint may also be spelled `line`, `line_num`, `line_no`, or
  `line_number`.

The prose that teaches an agent *when* to prefer each form lives in the system prompt's edit
examples, not in per-argument descriptions — `EditFileCore.parameter_properties()` is inlined
into every edit tool's schema and paid for on every turn, so its text stays terse.

This is purely additive: the classic five-field call is still valid and still the recommended
form for long replacement spans. Nothing about the on-disk result, the drift search, the
"Ambiguous match" recovery, or the result dict changes.

## Motivation

Two eval-adjacent frictions motivate this:

1. A one-line change forces the model to state the same line number twice (`start_line ==
   end_line`) and the same content twice-ish (`start_text` for line N, `end_text` for line N).
   For a single line those are literally the same line, so `end_text` is redundant ceremony that
   the model still has to get byte-perfect or eat a rejection.
2. A small multi-line replacement (say 2–5 lines) forces the model to mentally split the block
   into "first line → `start_text`", "last line → `end_text`", and "count the lines to get
   `end_line`". Models miscount `end_line`, or capture the wrong last line, and the call is
   rejected. Handing over the whole block as `old_text` and letting the harness count is both
   easier for the model and less error-prone.

Neither friction justifies loosening the safety property the drift-search ADR is careful about
(never silently editing the wrong location). Every new form still fails closed on a zero- or
multi-candidate match exactly as today.

## Current state (what exists today)

* `EditFileCore.apply()` (`klorb/src/klorb/tools/util/edit_file_core.py`) requires
  `start_line`, `end_line`, `start_text`, `end_text`, `new_text`; `context_before`/
  `context_after` are optional disambiguators.
* `start_text`/`end_text` are each truncated to their first line (with advisory `feedback`) if a
  model accidentally pastes a multi-line block, then hard-checked to be exactly one line.
* `_resolve_line_range_edit()` → `_find_drift_candidates()` locates `start_text` at
  `candidate_start` and `end_text` at `candidate_start + (end_line - start_line)`, within
  `ProcessConfig.edit_file_drift_search_radius` (default 20) of the hint. Exactly one candidate
  applies; zero raises a precise mismatch error; two-plus raises "Ambiguous match" naming each
  candidate's `context_before`/`context_after`.
* `parameter_properties()` returns the shared JSON schema; each tool wraps it with its own
  `filename` (or none, for scratchpad) and a `required` list. `EditFileTool.parameters()` sets
  `required = ["filename", "start_line", "end_line", "start_text", "end_text", "new_text"]` and
  `additionalProperties: False`.
* Callers: `EditFileTool`, `EditScratchpadTool`, `EditMemoryTool` all delegate to a
  `self.edit_file_core`. Any schema/`required` change must be mirrored in all three.

## The widened argument matrix

The single source of truth for what's accepted. "Line hint" = `start_line`, or (only in
`old_text` mode) one of its aliases. All forms still require `new_text` and a line hint.

### Accepted forms

| # | Form | Anchor | `end_line` | Result |
|---|------|--------|-----------|--------|
| 1 | Classic | `start_line`, `end_line`, `start_text`, `end_text` | required | Existing behavior, unchanged. |
| 2 | Single-line shortcut | `start_line == end_line`, `start_text`, `end_text` omitted/`""` | required (`== start_line`) | Replace the one line at the hint, anchored on `start_text`. |
| 3 | `old_text`, explicit span | `old_text`, `start_line`, `end_line` | required, **must** equal `start_line + count(old_text) - 1` | Replace the block; verified against `old_text`. |
| 4 | `old_text`, inferred span | `old_text`, `start_line` (or alias) | omitted | `end_line` inferred from `count(old_text)`. |
| 5 | `old_text` one-liner | `old_text` (1 line), `start_line` (or alias) | omitted | Single-line replace; a natural instance of form 4. |
| 6 | Multi-line `start_text` as `old_text` | `start_text` (multi-line), `end_text` omitted/`""`, `start_line` | omitted or explicit | `start_text` is re-interpreted as `old_text` (forms 3/4). |

Notes:

* **Form 2** — the single-line shortcut requires `start_line` and `end_line` to be *equal and
  both present*. It does not let `end_line` be omitted in `start_text` mode; a missing `end_line`
  with single-line `start_text` and no `old_text` is an error (there's no block to count and no
  second anchor). This keeps the "omit `end_line`" affordance exclusively in `old_text` mode,
  where it's unambiguous.
* **Form 6** — precedence rule: multi-line content in `start_text` is treated as `old_text`
  **only when `end_text` is empty or missing**. A multi-line `start_text` *with* a non-empty
  `end_text` keeps today's behavior: `start_text` is truncated to its first line with the
  existing advisory `feedback`, and `end_text` anchors the end. This preserves back-compat for
  any caller relying on the truncation nicety while giving the "I pasted the whole block into
  `start_text`" case a useful meaning.
* **Aliases** — `line`/`line_num`/`line_no`/`line_number` are consulted **only when `old_text`
  (or a form-6 multi-line `start_text`) is present**. In classic/`start_text` mode only
  `start_line` names the hint. If both `start_line` and an alias are present with the same value,
  fine; with conflicting values, error (see below).
* **`old_text` verification depth** — `old_text` is checked against the file *in full* (every
  line of the block, not just its first and last), reusing the fuzzy-whitespace fallback
  per-line. The caller already paid the tokens to send the whole block, so verifying all of it is
  free safety and strictly stronger than the endpoints-only check the `start_text`/`end_text`
  form uses. See "Design decisions" for why this doesn't reintroduce the token concern the
  drift-search ADR guards.

### Rejected forms (must raise a precise `ValueError`)

| Condition | Error intent |
|-----------|--------------|
| `old_text` **and** (`start_text` **or** `end_text`) both present | "Provide `old_text`, or `start_text`/`end_text`, not both." |
| `old_text` present, `end_line` present, `count(old_text) != end_line - start_line + 1` | Name both counts; suggest dropping `end_line` to let it be inferred. |
| Neither `old_text` nor `start_text` present | "No anchor: provide `start_text` (+`end_text`) or `old_text`." |
| No line hint in any spelling | Existing "Missing required argument" style, listing accepted spellings. |
| `start_line` and an alias both present with **different** values | "Conflicting line hints: `start_line=A` vs `line=B`." |
| Single-line `start_text` (no `old_text`), `end_text` omitted, `end_line != start_line` | "Omit-`end_text` is only for single-line edits (`start_line == end_line`); otherwise supply `end_text` or use `old_text`." |
| `new_text` missing | Existing behavior, unchanged. |
| `end_line < start_line`, or hint out of range | Existing behavior, unchanged. |
| `old_text == ""` (empty) | "`old_text` must be the non-empty block to replace; to insert into an empty file use `start_line=1, end_line=0, start_text=\"\", end_text=\"\"`." |

The empty-file insertion path (`start_line=1, end_line=0, start_text="", end_text=""`) is
untouched and remains the sole insert-into-empty form; it is deliberately *not* re-expressed in
`old_text` terms (there is no block to anchor).

## Implementation shape

All logic lands in `EditFileCore`; the three tool wrappers change only their `required` lists and
schema.

### 1. Argument normalization (new private step in `apply()`)

Before the existing type checks, add a `_normalize_edit_args(args)` step that returns a resolved
`(start_line, end_line, start_text, end_text, new_text, context_*)` tuple (or raises). It:

1. Reads `new_text` (unchanged requirement).
2. Resolves the **line hint**: `start_line` if present; else, only if `old_text`/form-6 applies,
   the first present alias among `line`, `line_num`, `line_no`, `line_number`. Conflicting
   distinct values → error.
3. Detects **`old_text` mode** (`old_text` present and non-empty) and **form-6** (multi-line
   `start_text` with empty/missing `end_text`); form-6 sets `old_text = start_text` and clears
   `start_text`. Enforces the "not both" and empty-`old_text` rejections.
4. In `old_text` mode: split `old_text` into lines; derive `start_text = lines[0]`,
   `end_text = lines[-1]`, `block_len = len(lines)`. If `end_line` given, verify `end_line -
   start_line + 1 == block_len` (mismatch → error); else set `end_line = start_line + block_len -
   1`. Stash the full `old_text` line list so `_resolve_line_range_edit` can verify the interior
   (see step below).
5. In single-line `start_text` mode with `end_text` omitted/empty: require `end_line ==
   start_line` (or `end_line` omitted → treat as `start_line`); set `end_text = start_text`.
6. Otherwise (classic): behave exactly as today, including the multi-line-`start_text`-with-
   `end_text` truncation nicety.

Keeping this as one normalization function means `_resolve_line_range_edit` and
`_find_drift_candidates` stay almost as they are — they still receive a concrete `start_text`/
`end_text`/`start_line`/`end_line`. The one addition they need is an optional
`block_lines: list[str] | None` (the full `old_text` lines) so a candidate is accepted only if
the whole span matches, not just its endpoints. When `block_lines is None` (classic/`start_text`
form) the endpoints-only check is unchanged.

### 2. Full-block verification for `old_text`

`_find_drift_candidates` gains an optional `block_lines` parameter. When present, a candidate at
`candidate_start` must satisfy `all_lines[candidate_start-1 : candidate_start-1+block_len]`
equals `block_lines` (exact), with the fuzzy pass comparing `.strip()` per line — supplanting the
first/last-line-only anchor check for that call. `context_before`/`context_after` still apply
unchanged. This is a strict superset of the endpoints check, so a genuine wrong-location match is
*less* likely, never more.

### 3. Result dict / feedback

Unchanged shape (`requested_start_line`, `requested_end_line`, `start_line`, `end_line`,
`line_hint_matched`, `new_total_lines`, `content`, optional `fuzzyWhitespaceMatch`/`whitespace`/
`feedback`). When `end_line` was inferred (not supplied), `requested_end_line` echoes the
inferred value. Add a terse `feedback` note when an alias spelling was used
("btw, the line hint is `start_line`; `line_no` was accepted this time"), consistent with the
existing token-saving nudges — but keep these advisory, never errors.

### 4. Schema and `required` changes (all three tool wrappers + core)

* `parameter_properties()` adds:
  * `old_text` — one terse line: "Alternative to start_text/end_text: the entire current block
    to replace, verbatim. end_line then optional (inferred from its line count)."
  * `line`, `line_num`, `line_no`, `line_number` — bare `{"type": "integer"}` entries with **no
    description** (near-zero repeated-token cost), so `additionalProperties: False` still admits
    them. Their meaning is documented once in the system prompt, not per-property.
* `start_text`, `end_text`, `end_line` descriptions get a terse "(optional in some forms — see
  system prompt)" and are **removed from every wrapper's `required` list**. New `required`:
  * `EditFileTool`: `["filename", "start_line", "new_text"]`
  * `EditScratchpadTool`: `["start_line", "new_text"]`
  * `EditMemoryTool`: mirror scratchpad (whatever identifying field it carries) + `new_text`.
  * (The line hint stays in `required` as `start_line`; aliases are a tolerance net for when a
    model ignores the schema, not the advertised spelling.)

Because the accept/reject logic now lives in `apply()` with rich `ValueError`s, relaxing the
schema `required` doesn't lose any guardrail — it moves the guardrail somewhere that can explain
itself.

## Prompt changes (system prompt, not arg descriptions)

Per the terseness constraint, `parameter_properties()` text stays minimal; the teaching goes in
`klorb/src/klorb/resources/system_prompts.d/default_sys.md`'s "Editing files" section, which
already carries the worked example. Add:

* A one-line **decision rule** near "The rule:":
  > For a long multi-line replacement, `start_line`/`end_line` + one-line `start_text`/`end_text`
  > is the most token-efficient anchor (you never repeat the interior). For a short block (~1–5
  > lines), `old_text` (the whole block, verbatim) is easier and often more compact — the harness
  > infers `end_line` for you. Never send both.
* A note on the **single-line shortcut**: when replacing exactly one line, set
  `start_line == end_line` and omit `end_text`; `start_text` alone anchors it.
* A **second worked example** alongside the existing one, showing the `old_text` form for a
  2–3 line change (call + response), so the model sees both idioms side by side. Keep the
  existing `start_text`/`end_text` worked example as the long-span exemplar.
* One line reminding that `old_text` is fuzzy-anchored to the line hint with the same drift
  tolerance, so a slightly-stale line number still resolves.

`EditMemory`/`EditScratchpad` inherit this section (the memories note already says "same edit
mechanics as EditFile above"); no per-tool prose duplication.

## Tests (`klorb/tests/test_edit_file.py`)

The widened matrix multiplies the argument combinations, so enumerate and unit-test them all
against `EditFileCore.apply()` with mocked args (these tests exercise `apply()` logic, not a live
model — that's what evals are for). Group them:

**Accepted forms — each asserts the correct file bytes and result dict:**

1. Classic five-field (regression — still works).
2. Single-line shortcut: `start_line == end_line`, `end_text` omitted.
3. Single-line shortcut: `start_line == end_line`, `end_text = ""`.
4. `old_text` multi-line, `end_line` omitted (inferred).
5. `old_text` multi-line, `end_line` supplied and matching.
6. `old_text` one line, `end_line` omitted.
7. Form-6: multi-line `start_text`, `end_text` omitted → treated as `old_text`.
8. `old_text` with each alias spelling (`line`, `line_num`, `line_no`, `line_number`), one test
   each, `start_line` absent.
9. `old_text` with `start_line` *and* a matching-value alias present (dedup, no error).
10. `old_text` block that has drifted from the hint within the radius → relocates and applies
    (`line_hint_matched=False`), full-block verified.
11. `old_text` block whose interior line differs from the file → **no** candidate → precise
    mismatch error (proves full-block verification, not endpoints-only).
12. `old_text` with a fuzzy-whitespace-only difference on one interior line → resolves via the
    fuzzy pass, sets `fuzzyWhitespaceMatch`.

**Rejected forms — each asserts a `ValueError` whose message names the specific problem:**

13. `old_text` + `start_text` both present.
14. `old_text` + `end_text` both present.
15. `old_text` + `end_line` present but line count disagrees.
16. Neither `old_text` nor `start_text`.
17. No line hint in any spelling.
18. `start_line` + alias with conflicting values.
19. Single-line `start_text`, `end_text` omitted, `end_line != start_line`.
20. `old_text = ""`.
21. Multi-line `start_text` *with* non-empty `end_text` → still truncates + advisory (assert
    `feedback` present and edit lands on first-line anchor), i.e. form-6 does **not** trigger.
22. `new_text` missing (regression).
23. Out-of-range / `end_line < start_line` (regression).

Parametrize where the bodies are identical across spellings (e.g. #8's four aliases). Mirror a
representative subset for `EditScratchpad`/`EditMemory` via their existing test modules to prove
the shared core reaches all three tools (no need to re-run the full matrix three times — one
smoke test per wrapper that the new forms are wired through).

## Evals (`klorb/evals/cases.py`)

**Answering the question posed in the task — should we stress single vs. multi-line / repeated
edits more?** Yes, and specifically to prove the *new forms actually get used*, not just that the
file ends up right. Evals grade filesystem state (per
[[grade-tool-evals-by-filesystem-state]]), but several existing cases already inspect
`session.messages` for the arguments actually sent (the ambiguous-match cases do this). We use the
same "where useful" carve-out to assert the *shape* of the call where the whole point is that a
new shape was available. Proposed new cases:

1. **`edit_file_single_line_shortcut`** — a one-line change; assert the file is right *and* that
   at least one `EditFile` call used the shortcut (`end_text` absent/empty with `start_line ==
   end_line`). Prioritizes proving form 2 is reachable. **(Priority 1.)**
2. **`edit_file_old_text_small_block`** — replace a 3-line block; assert result + that an
   `EditFile` call carried `old_text` and omitted `end_line`. Proves forms 3–4. **(Priority 1.)**
3. **`edit_file_old_text_vs_start_end_long_span`** — a ~15-line replacement where `start_text`/
   `end_text` is the efficient form; grade on state + `expected_tool_calls` (a model that tried
   to stuff 15 lines into `old_text` still passes state-wise, but the token/efficiency signal
   shows up if we later add a token check). Mostly a companion to #2 to cover the "long span →
   prefer start/end" half of the decision rule. **(Priority 3.)**
4. **`edit_file_old_text_drifted`** — the `old_text` block has been shifted by an earlier edit in
   the same turn; assert it still lands (drift + full-block verification). **(Priority 2.)**
5. **`edit_file_old_text_repeated_block`** — two identical multi-line blocks in the file; the
   `old_text` full-block match plus a line hint should resolve the intended one without needing
   `context_*` (contrast with the single-line ambiguous-match cases, where full-block context
   isn't available). Assert the correct block changed. **(Priority 2.)**
6. **`edit_file_many_old_text_ops_one_turn`** — several small `old_text` block edits in one turn,
   bottom-to-top, mirroring `EDIT_FILE_MANY_OPS_BOTTOM_TO_TOP` but for the block form. Lower
   priority; #4 already covers the drift interaction. **(Priority 4.)**

Recommend landing priorities 1–2 (cases 1, 2, 4, 5) with this plan and holding 3 and 6 unless the
first four surface gaps. Add each to the `CASES` list and give each a realistic
`expected_tool_calls` so a stumbled retry shows as a conditional pass.

### Eval harness `--self-review` flag

Add a `--self-review` argument to `klorb/evals/run_evals.py` that closes the loop: feed the eval
output back to the agent under test and ask it to propose tool improvements.

Behavior when `--self-review` is passed:

1. Run the suite as normal.
2. Write the **full** eval output (the same detailed per-case transcript + summary
   `_write_eval_log` already produces, un-colorized) to a file in a fresh temp directory that the
   agent is granted read access to.
3. Start a review `Session` (same model/provider) whose `read_dirs.allow` includes that temp
   directory, with a prompt that:
   * tells it the path to the results file and **reminds it to use its `ReadFile` tool** to read
     it (it can page a long file);
   * asks it to diagnose which tool ergonomics caused failures/retries and propose concrete
     changes to the edit tools that would raise the success rate;
   * instructs it to **number and prioritize** its suggestions and emit the **top five at most**.
4. Print the agent's self-diagnosis message to the console for the user to read.

**The temp-dir + read-grant helper.** The task rightly flags that "mint a temp dir and grant the
agent `readDirs` on it" is a pattern we should have a library function for. We don't today — the
scratchpad tools *bypass* the permission tables entirely
([[scratchpad-tools-bypass-permission-tables]]) rather than granting a dir, and the eval harness
builds `read_dirs=DirRules(allow=[workspace_root])` inline. Add a small helper (proposed:
`klorb.permissions.directory_access.tempdir_readable_by_session()` or a
`klorb.tools.util` sibling) that:

* creates a `tempfile.mkdtemp()` directory (caller owns cleanup, or it returns a context
  manager),
* returns its `Path` alongside a `DirRules` (or appends to an existing `SessionConfig.read_dirs`)
  with that path in `.allow`, canonicalized via `canonicalize_dir`.

The eval `--self-review` path is its first caller; during implementation, grep for other inline
`DirRules(allow=[...])` construction sites (the harness's own, at minimum) and migrate any that
are really "grant read on this scratch location" to the helper, so the abstraction earns its
keep. If it turns out the eval path is genuinely the only site, still add the helper as the task
requests, but keep it minimal.

Keep `--self-review` off by default (a normal `make evals` run is unchanged); it's an opt-in
investigative mode, and it costs a second model round-trip.

## Design decisions to confirm with the reviewer

1. **Full-block `old_text` verification vs. endpoints-only.** Recommended: verify the whole
   block. It's strictly safer and the tokens are already spent. The drift-search ADR's
   "don't recapitulate large spans just to anchor" concern was about *forcing* the caller to
   send interior content they otherwise wouldn't; here the caller *chose* `old_text` and sent it,
   so verifying it isn't extra burden. Confirm this reading.
2. **Aliases in schema as bare no-description properties vs. `additionalProperties` relaxation.**
   Recommended: bare `{"type": "integer"}` properties, keeping `additionalProperties: False`.
   Cheaper and safer than opening the schema. Confirm.
3. **`required` relaxed to `["filename", "start_line", "new_text"]`** (validation moved into
   `apply()`), rather than an `anyOf`/`oneOf` schema expressing "start_text-or-old_text". `anyOf`
   is poorly and inconsistently honored across providers and would bloat the repeated schema;
   in-`apply()` validation with explanatory errors is the codebase's existing idiom. Confirm.
4. **Form-6 precedence** (multi-line `start_text` → `old_text` *only* when `end_text` is
   empty/missing; otherwise keep today's truncation). Confirm this back-compat carve-out is
   wanted rather than simply always treating multi-line `start_text` as `old_text`.

## Out of scope

* No change to the drift search radius, the "Ambiguous match" machinery, or `context_before`/
  `context_after`.
* No batch/multi-edit API (rejected in the drift-search ADR; unchanged here).
* No change to `ReadFile`/`ReplaceAll`/other tools.
* No new token-cost check in the eval report (case #3 above is written so one could be added
  later, but adding it is a separate change).
* Turning any of this plan's durable behavior into a spec happens at implementation time, per
  README-PLANS: fold the accepted-argument contract into `docs/specs/` and record the
  endpoints-vs-full-block and schema-`required` decisions as ADRs.

## Files touched (anticipated)

* `klorb/src/klorb/tools/util/edit_file_core.py` — normalization, `old_text`, aliases,
  single-line shortcut, full-block verification, schema properties.
* `klorb/src/klorb/tools/edit_file.py`, `.../tools/scratchpad/edit.py`,
  `.../tools/memory/edit_memory.py` — relaxed `required` lists.
* `klorb/src/klorb/resources/system_prompts.d/default_sys.md` — edit examples + decision rule.
* `klorb/tests/test_edit_file.py` (+ small smoke additions to the scratchpad/memory edit tests).
* `klorb/evals/cases.py`, `klorb/evals/run_evals.py` — new cases + `--self-review`.
* `klorb/src/klorb/permissions/directory_access.py` (or a `klorb.tools.util` sibling) — the
  tempdir-readable helper.
* New ADR(s) under `docs/adrs/`; new/updated spec under `docs/specs/` at implementation time.
